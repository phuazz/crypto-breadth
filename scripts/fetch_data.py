"""
fetch_data.py
-------------
Pulls daily OHLCV for the fixed top-10 crypto majors universe from Binance
via CCXT, from 2018-01-01 to today. Writes to data/prices.parquet.

Investability flags are derived from the first non-null observation per symbol
(no survivorship bias: if a coin had no Binance pair in 2018, it is treated
as uninvestable for the breadth and ranking calculations until its listing date).

Run cadence: once daily (e.g. via GitHub Actions cron at 00:30 UTC).
Idempotent: safe to re-run; existing data is overwritten with the full pull.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import ccxt


# ----- configuration -------------------------------------------------------

# Expanded candidate universe — 25 USDT pairs on Binance spot. The strategy
# uses a ROLLING liquidity-gated investability rule (see backtest.py); this
# list is the candidate POOL, not the active book.
#
# Composition:
#   Core 10 (original survivor-10):
#     BTC, ETH, BNB, SOL, XRP, ADA, DOGE, AVAX, DOT, LINK
#   Historical majors that the survivor-10 omitted:
#     LTC, BCH, TRX, EOS, ETC, XLM, ATOM, MATIC, UNI, AAVE, NEAR, ALGO, FIL
#   Failed coins (included for survivorship honesty — both crashed during
#   the sample and were subsequently delisted):
#     LUNA (May 2022), FTT (November 2022)
#
# Coins enter the active investable set only when they meet a rolling
# liquidity threshold (trailing 30d ADV ≥ $25 M and ≥ 90 days of history).
UNIVERSE: list[str] = [
    # Core 10
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
    "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "DOT/USDT", "LINK/USDT",
    # Historical majors
    "LTC/USDT", "BCH/USDT", "TRX/USDT", "EOS/USDT", "ETC/USDT",
    "XLM/USDT", "ATOM/USDT", "MATIC/USDT", "UNI/USDT", "AAVE/USDT",
    "NEAR/USDT", "ALGO/USDT", "FIL/USDT",
    # Failed coins
    "LUNA/USDT", "FTT/USDT",
]

START_DATE = "2018-01-01"
TIMEFRAME = "1d"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_PATH = DATA_DIR / "prices.parquet"
META_PATH = DATA_DIR / "prices_meta.json"

# Binance returns max 1000 candles per request; with daily candles that is
# ~2.7 years per call. We loop until we reach 'now'.
BATCH_LIMIT = 1000
RATE_LIMIT_SLEEP_S = 0.25  # be polite to public endpoint


# ----- core fetch ----------------------------------------------------------

def make_exchange() -> ccxt.binance:
    """Public client — no API keys needed for historical klines."""
    return ccxt.binance({
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })


def fetch_symbol(exchange: ccxt.binance, symbol: str, since_ms: int) -> pd.DataFrame:
    """Fetch full daily OHLCV history for one symbol from `since_ms` to now.

    Returns DataFrame indexed by UTC date with columns: open, high, low, close, volume.
    Empty DataFrame if symbol is not listed (no data returned at all).
    """
    rows: list[list] = []
    cursor = since_ms

    while True:
        try:
            batch = exchange.fetch_ohlcv(
                symbol, timeframe=TIMEFRAME, since=cursor, limit=BATCH_LIMIT
            )
        except ccxt.BadSymbol:
            # Pair never existed on Binance — return empty.
            return pd.DataFrame()
        except Exception as e:
            # Transient error — retry once after a longer pause, then give up.
            print(f"  warn: {symbol} fetch error at cursor={cursor}: {e!r} — retrying once")
            time.sleep(2.0)
            batch = exchange.fetch_ohlcv(
                symbol, timeframe=TIMEFRAME, since=cursor, limit=BATCH_LIMIT
            )

        if not batch:
            break

        rows.extend(batch)
        last_ts = batch[-1][0]

        # If we got fewer than BATCH_LIMIT, we are at the end.
        if len(batch) < BATCH_LIMIT:
            break

        # Advance cursor to one ms past last candle to avoid duplicates.
        cursor = last_ts + 1
        time.sleep(RATE_LIMIT_SLEEP_S)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["ts_ms", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True).dt.tz_convert(None).dt.normalize()
    df = df.drop(columns=["ts_ms"]).drop_duplicates(subset=["date"]).set_index("date").sort_index()
    return df


def fetch_universe(universe: list[str], start_date: str) -> pd.DataFrame:
    """Fetch all symbols and stack into a long DataFrame.

    Output columns: symbol, open, high, low, close, volume (indexed by date).
    """
    exchange = make_exchange()
    since_ms = int(pd.Timestamp(start_date, tz="UTC").timestamp() * 1000)

    frames: list[pd.DataFrame] = []
    for sym in universe:
        print(f"fetching {sym} ...")
        df = fetch_symbol(exchange, sym, since_ms)
        if df.empty:
            print(f"  no data for {sym} — skipping")
            continue
        df = df.assign(symbol=sym.replace("/USDT", ""))
        first_date = df.index.min().date()
        last_date = df.index.max().date()
        print(f"  {sym}: {len(df)} rows, {first_date} → {last_date}")
        frames.append(df)

    if not frames:
        raise RuntimeError("No data fetched for any symbol — check network/CCXT.")

    out = pd.concat(frames).reset_index().sort_values(["symbol", "date"]).reset_index(drop=True)
    return out


# ----- main ----------------------------------------------------------------

def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching {len(UNIVERSE)} symbols from {START_DATE} ...")
    df = fetch_universe(UNIVERSE, START_DATE)

    # Investability metadata: first available date per symbol.
    investability = (
        df.groupby("symbol")["date"]
        .agg(["min", "max", "count"])
        .rename(columns={"min": "first_date", "max": "last_date", "count": "n_obs"})
        .reset_index()
    )
    print("\nInvestability summary:")
    print(investability.to_string(index=False))

    df.to_parquet(OUT_PATH, index=False)
    investability.to_json(META_PATH, orient="records", date_format="iso", indent=2)

    print(f"\nWrote {len(df):,} rows → {OUT_PATH}")
    print(f"Wrote metadata → {META_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
