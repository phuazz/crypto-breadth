"""
fetch_daily_update.py
---------------------
Incremental daily update for the prices parquet. Designed for GitHub
Actions, where Binance's TRADING host (api.binance.com) returns HTTP 451
"restricted location" from US runners.

Data source: Binance's public MARKET-DATA mirror,
``data-api.binance.vision``. This host serves the identical spot klines as
api.binance.com but is NOT geo-restricted (it exists precisely to serve
market data globally, including from US IPs). Critically, this means the
operational daily tail is now drawn from the SAME substrate as the frozen
research history in prices.parquet (Binance USDT spot) — there is no longer
a cross-vendor splice to police (contrast the retired CryptoCompare path;
see DATA_INTEGRITY_POLICY.md §4).

Why this replaced CryptoCompare (2026-07-14):
  - CryptoCompare's free histoday tier is rate-limited (~11 calls/min) and
    its key had exhausted, freezing the parquet at 2026-07-04 for ten days
    while the CI reported green (the fetch step was continue-on-error).
  - The mirror needs no API key, has no per-minute cap at our 25-coin
    cadence (klines weight 2; 1200 weight/min budget), and is the same
    exchange as the research substrate.

Strategy:
  1. Load the existing data/prices.parquet (the full Binance history
     bootstrapped locally via scripts/fetch_data.py).
  2. For each coin, find the most-recent observed date.
  3. Fetch only the gap from the mirror's klines endpoint. Keep only
     CLOSED daily candles (the current UTC day is still forming at the
     00:45 UTC cron and must not be ingested — drop_duplicates(keep="first")
     would otherwise lock in a partial candle permanently).
  4. Append the new rows, drop duplicates, save.

Delisted / rebranded pairs: EOS and MATIC were rebranded on Binance
(MATIC -> POL in 2024; EOS -> A/Vaulta in 2025), so their legacy *USDT
pairs return no data. Both are OUTSIDE the investable set, so freezing them
has no effect on the live signal or breadth. They are listed in
DELISTED_ON_BINANCE and treated as expected-frozen rather than fetch
failures. Whether to remap them to the successor pairs (POLUSDT / AUSDT) is
a token-migration splice decision that belongs to the Phase-2 survivorship
audit, not this script.

If a coin that is NOT known-delisted returns no data or the API fails, the
script exits non-zero. The dashboard cannot silently publish on partial
data — either every live coin updates, or CI fails loud and we investigate.
A sidecar data/fetch_status.json is always written first (whether or not
the script then exits 1) so pipeline.py can still show which coins updated
and which lagged.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Env overrides let a dry-run point at a scratch copy without touching the
# production parquet. CI sets neither, so it uses the canonical paths.
PARQUET_PATH = Path(os.environ.get("CB_PARQUET_PATH") or (PROJECT_ROOT / "data" / "prices.parquet"))
STATUS_PATH = Path(os.environ.get("CB_STATUS_PATH") or (PROJECT_ROOT / "data" / "fetch_status.json"))

# Our universe symbols map 1:1 to Binance spot pairs as SYMBOL + "USDT".
UNIVERSE: list[str] = [
    "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "AVAX", "DOT", "LINK",
    "LTC", "BCH", "TRX", "EOS", "ETC", "XLM", "ATOM", "MATIC", "UNI", "AAVE",
    "NEAR", "ALGO", "FIL", "LUNA", "FTT",
]

# Pairs delisted / rebranded on Binance spot — their legacy SYMBOLUSDT pair
# returns no candles. All are OUTSIDE the current investable set, so a frozen
# tail here does not perturb the live signal. Returning no data for these is
# EXPECTED and does not trip the fail-loud exit.
#   MATIC -> POL   (rebrand, Sep 2024)
#   EOS   -> A     (Vaulta rebrand, 2025)
DELISTED_ON_BINANCE: set[str] = {"EOS", "MATIC"}

# Binance market-data mirror — not geo-restricted (unlike api.binance.com).
KLINES_ENDPOINT = "https://data-api.binance.vision/api/v3/klines"
KLINES_LIMIT = 1000            # one call spans ~2.7y of daily candles; no paging needed for a tail
REQUEST_TIMEOUT_S = 30
POLITE_SLEEP_S = 0.1           # trivial spacing; well within the weight budget

MS_PER_DAY = 86_400_000


def fetch_binance_daily(pair: str, start_ms: int, now_ms: int) -> pd.DataFrame:
    """Fetch CLOSED daily klines for `pair` with open-time >= start_ms.

    Returns a DataFrame with columns date, open, high, low, close, volume
    (base-asset volume, matching CCXT's convention in fetch_data.py). Only
    candles whose close-time is in the past are kept, so the still-forming
    current UTC-day candle is never ingested. An empty DataFrame means the
    pair returned no candles (delisted / rebranded).
    """
    params = {
        "symbol": pair,
        "interval": "1d",
        "startTime": int(start_ms),
        "limit": KLINES_LIMIT,
    }
    r = requests.get(KLINES_ENDPOINT, params=params, timeout=REQUEST_TIMEOUT_S)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return pd.DataFrame()

    # Binance kline layout: [openTime, open, high, low, close, volume,
    # closeTime, quoteVolume, trades, takerBase, takerQuote, ignore].
    df = pd.DataFrame(rows, columns=[
        "open_ms", "open", "high", "low", "close", "volume",
        "close_ms", "quote_vol", "trades", "taker_base", "taker_quote", "ignore",
    ])
    # Keep only fully-closed candles (close-time strictly in the past).
    df = df[df["close_ms"] < now_ms].copy()
    if df.empty:
        return pd.DataFrame()

    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["open_ms"], unit="ms", utc=True).dt.tz_convert(None).dt.normalize()
    return df[["date", "open", "high", "low", "close", "volume"]]


def main() -> int:
    if not PARQUET_PATH.exists():
        print(f"  ERROR: parquet not found at {PARQUET_PATH}")
        print("  Run scripts/fetch_data.py once locally to bootstrap, then commit.")
        return 1

    existing = pd.read_parquet(PARQUET_PATH)
    existing["date"] = pd.to_datetime(existing["date"])
    last_dates = existing.groupby("symbol")["date"].max()

    now_utc = datetime.now(timezone.utc)
    now_ms = int(now_utc.timestamp() * 1000)
    today = pd.Timestamp(now_utc.date())

    print(f"Loaded {len(existing):,} existing rows, "
          f"latest data point at {existing['date'].max().date()}")
    print(f"Now (UTC): {now_utc.isoformat()}  target end: {today.date()}")
    print()

    new_rows: list[pd.DataFrame] = []
    updated: list[dict] = []
    skipped: list[dict] = []

    for sym in UNIVERSE:
        last = last_dates.get(sym)
        if last is None:
            skipped.append({"symbol": sym, "reason": "not_in_parquet"})
            continue
        gap = (today - last).days
        if gap <= 0:
            continue  # already current

        pair = f"{sym}USDT"
        start_ms = int(last.timestamp() * 1000) + MS_PER_DAY  # day after last observed
        print(f"  {sym} ({pair}): last {last.date()}, gap {gap}d ...", flush=True)
        try:
            df = fetch_binance_daily(pair, start_ms=start_ms, now_ms=now_ms)
        except Exception as e:
            print(f"    error: {e!r}")
            skipped.append({
                "symbol": sym,
                "reason": "fetch_error",
                "detail": f"{type(e).__name__}: {e!s}"[:200],
            })
            continue

        # Keep only strictly-new dates so we never overwrite Binance history.
        if not df.empty:
            df = df[df["date"] > last]

        if df.empty:
            if sym in DELISTED_ON_BINANCE:
                # Expected: legacy pair no longer trades. Not a failure.
                print(f"    delisted/rebranded on Binance — frozen (non-investable)")
                skipped.append({"symbol": sym, "reason": "delisted_frozen"})
            else:
                print(f"    no rows returned")
                skipped.append({"symbol": sym, "reason": "no_data"})
            continue

        df = df.assign(symbol=sym)
        new_rows.append(df)
        updated.append({
            "symbol": sym,
            "n_appended": int(len(df)),
            "new_last_date": str(df["date"].max().date()),
        })
        print(f"    +{len(df)} new rows up to {df['date'].max().date()}")
        time.sleep(POLITE_SLEEP_S)

    if skipped:
        print(f"\nSkipped: {[s['symbol'] + ':' + s['reason'] for s in skipped]}")

    if new_rows:
        new_data = pd.concat(new_rows, ignore_index=True)
        combined = pd.concat([existing, new_data], ignore_index=True)
        combined = (
            combined.drop_duplicates(subset=["symbol", "date"], keep="first")
                    .sort_values(["symbol", "date"])
                    .reset_index(drop=True)
        )
        combined.to_parquet(PARQUET_PATH, index=False)
        print(f"\nAppended {len(new_data)} rows -> {PARQUET_PATH}")
        print(f"Parquet now: {len(combined):,} rows total, "
              f"latest {combined['date'].max().date()}")
    else:
        print("\nNo new data to append. Parquet unchanged.")

    # Always emit a status sidecar so pipeline.py / the dashboard can render
    # which coins succeeded and which lagged on the last run. Written BEFORE
    # we decide to exit non-zero, so a follow-up manual pipeline rebuild can
    # still surface the breakage in the UI. delisted_frozen skips are counted
    # separately so they do NOT force the fail-loud exit.
    hard_fail = [s for s in skipped if s["reason"] != "delisted_frozen"]
    status = {
        "generated_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "today_utc": str(today.date()),
        "source": "binance_data_vision",
        "updated": updated,
        "skipped": skipped,
        "n_total": len(UNIVERSE),
        "n_updated": len(updated),
        "n_skipped": len(skipped),
        "n_hard_fail": len(hard_fail),
    }
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(f"Wrote status sidecar -> {STATUS_PATH}")

    if hard_fail:
        # Fail loud rather than silently publish a partial refresh. Only
        # unexpected failures count — delisted_frozen (EOS/MATIC) is normal.
        print(f"\nFAILED: {len(hard_fail)} live coin(s) could not be updated. "
              f"See {STATUS_PATH.name}. Refusing to publish a partial refresh — "
              f"fix the upstream fetch or update UNIVERSE / DELISTED_ON_BINANCE.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
