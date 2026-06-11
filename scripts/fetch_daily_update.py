"""
fetch_daily_update.py
---------------------
Incremental daily update for the prices parquet. Designed for GitHub
Actions, where Binance returns HTTP 451 "restricted location" from US
runners.

Strategy:
  1. Load the existing data/prices.parquet (which has the full Binance
     history from the original scripts/fetch_data.py run).
  2. For each coin in the universe, find the most-recent observed date.
  3. Fetch only the gap from CryptoCompare's free histoday endpoint
     (USDT-quoted to mirror our Binance pair convention).
  4. Append the new rows, drop duplicates, save.

CryptoCompare is used because:
  - Free, no auth required, no rate-limit headaches at our 25-coin × 1-day
    cadence
  - USDT quoting available (closest match to the historical Binance pairs)
  - Works from US IPs (the actual reason we're not using Binance here)

The volume column is NOT identical to Binance's (CryptoCompare aggregates
across exchanges). For the rolling-30-day ADV liquidity gate, this is
acceptable noise — the threshold is $25M which is well above any borderline
exchange-specific variation for our majors universe.

If a coin is missing on CryptoCompare or the API fails, the script
exits non-zero. The dashboard cannot silently publish on partial data —
either every coin updates, or CI fails loud and we investigate. A
sidecar `data/fetch_status.json` is always written first (whether or
not the script then exits 1) so pipeline.py can still show which coins
were updated and which lagged.
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
PARQUET_PATH = PROJECT_ROOT / "data" / "prices.parquet"
STATUS_PATH = PROJECT_ROOT / "data" / "fetch_status.json"

# Map our universe symbols to CryptoCompare's tickers.
# Most match 1:1. LUNA was renamed to LUNC after the May 2022 crash;
# CryptoCompare uses LUNC. FTT was delisted from most venues but CC
# still has the historical series.
CC_TICKERS = {
    "BTC": "BTC", "ETH": "ETH", "BNB": "BNB", "SOL": "SOL",
    "XRP": "XRP", "ADA": "ADA", "DOGE": "DOGE", "AVAX": "AVAX",
    "DOT": "DOT", "LINK": "LINK",
    "LTC": "LTC", "BCH": "BCH", "TRX": "TRX", "EOS": "EOS",
    "ETC": "ETC", "XLM": "XLM", "ATOM": "ATOM", "MATIC": "MATIC",
    "UNI": "UNI", "AAVE": "AAVE", "NEAR": "NEAR", "ALGO": "ALGO",
    "FIL": "FIL", "LUNA": "LUNC", "FTT": "FTT",
}

CC_ENDPOINT = "https://min-api.cryptocompare.com/data/v2/histoday"
RATE_LIMIT_SLEEP_S = 0.4
MAX_GAP_DAYS = 60  # ask for at most 60 days back per call (CC limit varies)
# CryptoCompare moved the free histoday endpoint behind required auth in
# June 2026 (silent change — every request returned 401). The key is
# passed via the Authorization header rather than a query parameter so
# it never lands in URL error logs on HTTP failures.
API_KEY_ENV = "CRYPTOCOMPARE_API_KEY"


def fetch_cc_daily(symbol: str, n_days: int, api_key: str) -> pd.DataFrame:
    """Fetch last `n_days` daily OHLCV rows from CryptoCompare.

    Quoted in USDT to match our Binance pair convention. CryptoCompare's
    `volumefrom` field is the base-asset volume (e.g. BTC), so it lines
    up with what CCXT returns for Binance.
    """
    params = {
        "fsym": symbol,
        "tsym": "USDT",
        "limit": max(1, min(n_days - 1, 2000)),
        "aggregate": 1,
    }
    headers = {"Authorization": f"Apikey {api_key}"}
    r = requests.get(CC_ENDPOINT, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    j = r.json()
    if j.get("Response") != "Success":
        msg = j.get("Message", "unknown")
        print(f"    CC error for {symbol}: {msg}")
        return pd.DataFrame()
    rows = j.get("Data", {}).get("Data", [])
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # CC returns 0/0/0/0/0 for days a pair did not exist — drop those
    df = df[df["close"] > 0].copy()
    if df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["time"], unit="s").dt.tz_localize(None).dt.normalize()
    out = df[["date", "open", "high", "low", "close", "volumefrom"]].rename(
        columns={"volumefrom": "volume"}
    )
    return out


def main() -> int:
    if not PARQUET_PATH.exists():
        print(f"  ERROR: parquet not found at {PARQUET_PATH}")
        print("  Run scripts/fetch_data.py once locally to bootstrap, then commit.")
        return 1

    api_key = os.environ.get(API_KEY_ENV) or ""
    if not api_key:
        print(f"  ERROR: ${API_KEY_ENV} is not set.")
        print(f"  CryptoCompare's histoday endpoint requires authentication.")
        print(f"  Generate a free key at https://www.cryptocompare.com/cryptopian/api-keys")
        print(f"  with the 'Poll Live and Historical Data' permission, then add it as a")
        print(f"  GitHub repo secret named {API_KEY_ENV}. README has the full instructions.")
        return 1

    existing = pd.read_parquet(PARQUET_PATH)
    existing["date"] = pd.to_datetime(existing["date"])
    last_dates = existing.groupby("symbol")["date"].max()
    today = pd.Timestamp(datetime.now(timezone.utc).date())

    print(f"Loaded {len(existing):,} existing rows, "
          f"latest data point at {existing['date'].max().date()}")
    print(f"Target end date: {today.date()}")
    print()

    new_rows: list[pd.DataFrame] = []
    updated: list[dict] = []
    skipped: list[dict] = []
    for our_sym, cc_sym in CC_TICKERS.items():
        last = last_dates.get(our_sym)
        if last is None:
            skipped.append({"symbol": our_sym, "reason": "not_in_parquet"})
            continue
        gap = (today - last).days
        if gap <= 0:
            continue  # already current — not counted as skip
        n_fetch = min(gap + 2, MAX_GAP_DAYS)
        print(f"  {our_sym} ({cc_sym}): last {last.date()}, gap {gap}d, "
              f"fetching {n_fetch}d ...", flush=True)
        try:
            df = fetch_cc_daily(cc_sym, n_days=n_fetch, api_key=api_key)
        except Exception as e:
            print(f"    error: {e!r}")
            skipped.append({
                "symbol": our_sym,
                "reason": "fetch_error",
                "detail": f"{type(e).__name__}: {e!s}"[:200],
            })
            continue
        if df.empty:
            print(f"    no rows returned")
            skipped.append({"symbol": our_sym, "reason": "no_data"})
            continue
        # Keep only strictly-new dates so we never overwrite Binance history
        df = df[df["date"] > last]
        if df.empty:
            # CC had rows but none were strictly newer — treat as current.
            continue
        df = df.assign(symbol=our_sym)
        new_rows.append(df)
        updated.append({
            "symbol": our_sym,
            "n_appended": int(len(df)),
            "new_last_date": str(df["date"].max().date()),
        })
        print(f"    +{len(df)} new rows up to {df['date'].max().date()}")
        time.sleep(RATE_LIMIT_SLEEP_S)

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
    # exactly which coins succeeded and which lagged on the last run. Written
    # BEFORE we decide to exit non-zero on partial failure, so a follow-up
    # manual pipeline rebuild can still surface the breakage in the UI.
    status = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "today_utc": str(today.date()),
        "updated": updated,
        "skipped": skipped,
        "n_total": len(CC_TICKERS),
        "n_updated": len(updated),
        "n_skipped": len(skipped),
    }
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(f"Wrote status sidecar -> {STATUS_PATH}")

    if skipped:
        # Fail loud rather than silently publish a partial refresh. The audit
        # finding was that the previous behaviour (skip + continue + publish)
        # let stale data ride for days without any alert. Exit non-zero so
        # the CI workflow halts before pipeline.py runs and the dashboard
        # stays on yesterday's good build until we investigate.
        print(f"\nFAILED: {len(skipped)} coin(s) could not be updated. "
              f"See data/fetch_status.json. Refusing to publish a partial "
              f"refresh — fix the upstream fetch or update CC_TICKERS.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
