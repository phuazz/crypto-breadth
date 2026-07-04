"""
No-look-ahead invariants — the load-bearing hygiene property.

Signals are observed at close T and traded at close T+1. These tests pin that
lag for BOTH the weekly rebalance and the daily trend-exit, and confirm the
breadth and momentum signals themselves only use past data.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest import run_backtest, momentum_score, breadth_pct_above_ma


def _flat_panel(n: int, cols=("A", "B")) -> pd.DataFrame:
    """Constant-price panel (zero drift) starting on a Monday, so weight changes
    are attributable only to executed trades, not price movement."""
    dates = pd.date_range("2021-01-04", periods=n, freq="D")  # 2021-01-04 = Monday
    return pd.DataFrame(100.0, index=dates, columns=list(cols))


def test_rebalance_target_realised_next_bar():
    """A target present on row T is realised at T+1 (lag_days=1), never same-bar."""
    close = _flat_panel(6, ("A", "B"))
    dates = close.index
    tgt = pd.DataFrame(np.nan, index=dates, columns=["A", "B"])
    tgt.loc[dates[1], :] = [1.0, 0.0]  # signal on row 1

    res = run_backtest(close, tgt, fee_bps_per_side=0.0, lag_days=1)
    w = res["weights"]

    assert w.loc[dates[1], "A"] == 0.0                 # not traded same bar
    assert abs(w.loc[dates[2], "A"] - 1.0) < 1e-12     # traded the next bar


def test_daily_trend_exit_realised_next_bar():
    """An exit flagged at end of T force-sells at the close of T+1 (1-bar lag)."""
    close = _flat_panel(7, ("A", "B"))
    dates = close.index
    tgt = pd.DataFrame(np.nan, index=dates, columns=["A", "B"])
    tgt.loc[dates[1], :] = [1.0, 0.0]                  # buy A, executes row 2
    exit_mask = pd.DataFrame(False, index=dates, columns=["A", "B"])
    exit_mask.loc[dates[3], "A"] = True               # exit flagged row 3

    res = run_backtest(
        close, tgt, fee_bps_per_side=0.0, lag_days=1, daily_exit_mask=exit_mask,
    )
    w = res["weights"]

    assert abs(w.loc[dates[3], "A"] - 1.0) < 1e-12    # still held on the flag day
    assert w.loc[dates[4], "A"] == 0.0                # sold one bar later
    assert int(res["daily_exit_count"].loc[dates[4]]) == 1


def test_momentum_score_ignores_future_prices():
    """Perturbing prices AFTER a cutoff must not change momentum scores up to it."""
    dates = pd.date_range("2021-01-01", periods=250, freq="D")
    rng = np.random.default_rng(0)
    path = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, size=(250, 3)), axis=0))
    px = pd.DataFrame(path, index=dates, columns=["A", "B", "C"])
    mask = px.notna()

    s1 = momentum_score(px, (30, 90, 180), mask)
    px2 = px.copy()
    px2.iloc[200:] *= 1.5  # change the future only
    s2 = momentum_score(px2, (30, 90, 180), mask)

    cut = dates[199]
    pd.testing.assert_frame_equal(s1.loc[:cut], s2.loc[:cut])


def test_breadth_ignores_future_prices():
    """Same past-only guarantee for the breadth signal that drives the gate."""
    dates = pd.date_range("2021-01-01", periods=120, freq="D")
    rng = np.random.default_rng(1)
    path = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, size=(120, 4)), axis=0))
    px = pd.DataFrame(path, index=dates, columns=list("ABCD"))
    mask = px.notna()

    b1 = breadth_pct_above_ma(px, 50, mask)
    px2 = px.copy()
    px2.iloc[100:] *= 0.5
    b2 = breadth_pct_above_ma(px2, 50, mask)

    cut = dates[99]
    pd.testing.assert_series_equal(b1.loc[:cut], b2.loc[:cut])
