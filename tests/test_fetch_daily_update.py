"""
Incremental daily-update fetch logic (scripts/fetch_daily_update.py).

These tests are network-free: the Binance mirror call (fetch_binance_daily) and
the wall clock are both monkeypatched, and the parquet is a tiny synthetic
panel in a tmp dir. They pin the boundary behaviour that a naive gap check gets
wrong, plus the fail-loud contract:

  - The current UTC day's candle is NOT closed yet, so holding yesterday's close
    (gap == 1) is FULLY CURRENT and must be a silent no-op, not a failure. (A
    gap<=0 check regressed this and reddened CI on 2026-07-14.)
  - A genuinely missing closed day (gap >= 2) is fetched and appended.
  - A delisted/rebranded pair (EOS/MATIC) returning nothing is expected and does
    NOT trip the fail-loud exit.
  - A LIVE coin returning nothing while behind DOES trip it (exit 1).
"""
from __future__ import annotations

from datetime import datetime as real_datetime, timezone

import pandas as pd
import pytest

import fetch_daily_update as fdu


FIXED_NOW = real_datetime(2026, 7, 14, 6, 0, 0, tzinfo=timezone.utc)  # a fixed "today"


class _FrozenClock:
    """Stand-in for datetime so `datetime.now(tz)` is deterministic."""
    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW if tz is None else FIXED_NOW.astimezone(tz)


def _make_parquet(path, last_date: str):
    """A minimal 25-symbol panel (every UNIVERSE symbol present) whose most
    recent row for each symbol is `last_date`. All symbols must exist or main()
    flags them not_in_parquet."""
    last = pd.Timestamp(last_date)
    dates = pd.date_range(last - pd.Timedelta(days=5), last, freq="D")
    frames = []
    for sym in fdu.UNIVERSE:
        frames.append(pd.DataFrame({
            "date": dates, "open": 1.0, "high": 1.0, "low": 1.0,
            "close": 1.0, "volume": 1.0, "symbol": sym,
        }))
    pd.concat(frames, ignore_index=True).to_parquet(path, index=False)


def _fake_fetch(empty_syms=()):
    """Return closed daily candles from start up to today-1 (the newest closed
    candle), or empty for `empty_syms` (simulating a delisted/rebranded pair)."""
    today = pd.Timestamp(FIXED_NOW.date())
    last_closed = today - pd.Timedelta(days=1)

    def _fetch(pair, start_ms, now_ms):
        sym = pair[:-4]  # strip trailing "USDT"
        if sym in empty_syms:
            return pd.DataFrame()
        start_date = pd.to_datetime(start_ms, unit="ms").normalize()
        rng = pd.date_range(start_date, last_closed, freq="D")
        if len(rng) == 0:
            return pd.DataFrame()
        return pd.DataFrame({
            "date": rng, "open": 1.0, "high": 1.0, "low": 1.0,
            "close": 1.0, "volume": 1.0,
        })
    return _fetch


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setattr(fdu, "datetime", _FrozenClock)
    monkeypatch.setattr(fdu, "PARQUET_PATH", tmp_path / "prices.parquet")
    monkeypatch.setattr(fdu, "STATUS_PATH", tmp_path / "fetch_status.json")
    return tmp_path


def test_current_day_is_noop_not_failure(wired, monkeypatch):
    """last == today-1 (2026-07-13): every live coin is current; only the
    delisted pairs are skipped. Must exit 0 — the boundary-bug regression."""
    _make_parquet(fdu.PARQUET_PATH, "2026-07-13")
    monkeypatch.setattr(fdu, "fetch_binance_daily",
                        _fake_fetch(empty_syms=fdu.DELISTED_ON_BINANCE))
    assert fdu.main() == 0
    # Parquet unchanged (no closed candle newer than 07-13 exists yet).
    df = pd.read_parquet(fdu.PARQUET_PATH)
    assert pd.to_datetime(df["date"]).max() == pd.Timestamp("2026-07-13")


def test_missing_closed_day_is_fetched(wired, monkeypatch):
    """last == today-3 (2026-07-11): the 07-12 and 07-13 closes are missing and
    must be appended (07-14 is still forming and must NOT appear)."""
    _make_parquet(fdu.PARQUET_PATH, "2026-07-11")
    monkeypatch.setattr(fdu, "fetch_binance_daily",
                        _fake_fetch(empty_syms=fdu.DELISTED_ON_BINANCE))
    assert fdu.main() == 0
    df = pd.read_parquet(fdu.PARQUET_PATH)
    btc = df[df["symbol"] == "BTC"]
    assert pd.to_datetime(btc["date"]).max() == pd.Timestamp("2026-07-13")


def test_delisted_empty_does_not_fail(wired, monkeypatch):
    """EOS/MATIC behind and returning nothing is expected — exit 0."""
    _make_parquet(fdu.PARQUET_PATH, "2026-07-04")  # behind by 10d
    monkeypatch.setattr(fdu, "fetch_binance_daily",
                        _fake_fetch(empty_syms=fdu.DELISTED_ON_BINANCE))
    assert fdu.main() == 0


def test_live_coin_empty_while_behind_hard_fails(wired, monkeypatch):
    """A LIVE coin (BTC) returning nothing while behind is a real breakage —
    exit 1 so CI goes red rather than publishing stale data."""
    _make_parquet(fdu.PARQUET_PATH, "2026-07-04")  # behind by 10d
    monkeypatch.setattr(fdu, "fetch_binance_daily",
                        _fake_fetch(empty_syms=set(fdu.DELISTED_ON_BINANCE) | {"BTC"}))
    assert fdu.main() == 1


# ===== the closed-candle filter inside fetch_binance_daily =====
# Everything above monkeypatches fetch_binance_daily wholesale, so its
# `close_ms < now_ms` filter — the thing that stops a still-forming candle being
# ingested as if it were final — was never exercised. That filter is what makes
# the cron TIME irrelevant to data integrity: the workflow was moved to 00:07
# UTC on 2026-08-20, only 7 minutes after the daily close, and the argument that
# this is safe rests entirely on these four assertions. Dates use pandas
# Timestamps throughout (Python months are 1-indexed).

def _kline_row(day: str):
    """One raw Binance daily kline for `day`, in the wire layout: openTime at
    that day's UTC midnight, closeTime one millisecond before the next."""
    open_ms = pd.Timestamp(day, tz="UTC").value // 1_000_000
    close_ms = open_ms + fdu.MS_PER_DAY - 1
    return [open_ms, "1.0", "2.0", "0.5", "1.5", "10.0",
            close_ms, "15.0", 100, "5.0", "7.5", "0"]


def _stub_klines(monkeypatch, days):
    """Serve `days` as the endpoint's response, unconditionally."""
    class _Resp:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return [_kline_row(d) for d in days]

    monkeypatch.setattr(fdu.requests, "get", lambda *a, **k: _Resp())


def _ms(ts: str) -> int:
    return pd.Timestamp(ts, tz="UTC").value // 1_000_000


@pytest.mark.parametrize("now, expected_last", [
    # Ordinary day: run at 00:03 UTC on the 20th. The 19th closed at
    # 23:59:59.999; the 20th is still forming and must be dropped.
    ("2026-08-20 00:03:00", "2026-08-19"),
    # MONTH boundary: 1 September picks up 31 August, not 1 September.
    ("2026-09-01 00:03:00", "2026-08-31"),
    # YEAR boundary: 1 January picks up 31 December of the prior year.
    ("2027-01-01 00:03:00", "2026-12-31"),
])
def test_forming_candle_is_never_ingested(monkeypatch, now, expected_last):
    days = ["2026-08-19", "2026-08-20", "2026-08-31", "2026-09-01",
            "2026-12-31", "2027-01-01"]
    _stub_klines(monkeypatch, days)
    df = fdu.fetch_binance_daily("BTCUSDT", start_ms=_ms("2026-08-01"),
                                 now_ms=_ms(now))
    assert not df.empty
    assert df["date"].max() == pd.Timestamp(expected_last)


def test_close_time_boundary_is_strict(monkeypatch):
    """close_ms == now_ms is NOT past — the candle is excluded at the exact
    millisecond it closes, and included one millisecond later. This is the
    assertion an early cron actually leans on."""
    _stub_klines(monkeypatch, ["2026-08-19"])
    close_ms = _ms("2026-08-20")  # = the 19th's close_ms + 1
    at_close = fdu.fetch_binance_daily("BTCUSDT", start_ms=_ms("2026-08-19"),
                                       now_ms=close_ms - 1)
    assert at_close.empty, "candle taken while still forming"
    just_after = fdu.fetch_binance_daily("BTCUSDT", start_ms=_ms("2026-08-19"),
                                         now_ms=close_ms)
    assert len(just_after) == 1
    assert just_after["date"].iloc[0] == pd.Timestamp("2026-08-19")
