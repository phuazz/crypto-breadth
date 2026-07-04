"""
Cost application and top-N ranking.

Fees are charged on every weight change (one-sided turnover × bps) and nowhere
else. Ranking selects the top-N by score at equal weight, degrades gracefully
when fewer than N names are eligible, and is deterministic under ties.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest import run_backtest, rank_top_n


def _flat_panel(n: int, cols=("A", "B")) -> pd.DataFrame:
    dates = pd.date_range("2021-01-04", periods=n, freq="D")  # Monday start
    return pd.DataFrame(100.0, index=dates, columns=list(cols))


def test_fee_charged_on_weight_change():
    """0 → 100% into A is one-sided turnover 1.0 → fee = 1.0 × 10bps = 0.001."""
    close = _flat_panel(4, ("A", "B"))
    dates = close.index
    tgt = pd.DataFrame(np.nan, index=dates, columns=["A", "B"])
    tgt.loc[dates[1], :] = [1.0, 0.0]

    res = run_backtest(close, tgt, fee_bps_per_side=10.0, lag_days=1)
    assert abs(res["fee_drag"].sum() - 0.001) < 1e-12
    assert abs(res["equity"].iloc[-1] - 0.999) < 1e-9


def test_no_fee_without_rebalance():
    """No target, no trade, no fee; flat prices → equity pinned at 1.0."""
    close = _flat_panel(4, ("A", "B"))
    dates = close.index
    tgt = pd.DataFrame(np.nan, index=dates, columns=["A", "B"])
    res = run_backtest(close, tgt, fee_bps_per_side=10.0, lag_days=1)
    assert res["fee_drag"].sum() == 0.0
    assert abs(res["equity"].iloc[-1] - 1.0) < 1e-12


def test_rank_top_n_picks_highest_equal_weight():
    idx = pd.date_range("2021-01-01", periods=1, freq="D")
    score = pd.DataFrame([[0.1, 0.9, 0.5, 0.3]], index=idx, columns=["A", "B", "C", "D"])
    row = rank_top_n(score, 2).iloc[0]
    assert row["B"] == 0.5 and row["C"] == 0.5   # top-2 at 1/2 each
    assert row["A"] == 0.0 and row["D"] == 0.0


def test_rank_top_n_handles_fewer_than_n():
    """Only one eligible name but n=4 → equal-weight what is available (100% A)."""
    idx = pd.date_range("2021-01-01", periods=1, freq="D")
    score = pd.DataFrame([[0.7, np.nan, np.nan]], index=idx, columns=["A", "B", "C"])
    row = rank_top_n(score, 4).iloc[0]
    assert row["A"] == 1.0
    assert abs(row.sum() - 1.0) < 1e-12


def test_rank_top_n_deterministic_under_ties():
    idx = pd.date_range("2021-01-01", periods=1, freq="D")
    score = pd.DataFrame([[0.5, 0.5, 0.5, 0.1]], index=idx, columns=["A", "B", "C", "D"])
    w1 = rank_top_n(score, 2)
    w2 = rank_top_n(score, 2)
    pd.testing.assert_frame_equal(w1, w2)
    assert abs(w1.iloc[0].sum() - 1.0) < 1e-12
