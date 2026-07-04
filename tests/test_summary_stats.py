"""
summary_stats — CAGR must use the wealth RATIO across the slice, not the final
level. This pins the documented fix: reading equity.iloc[-1] alone silently
misreports every sub-window that does not start at 1.0.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest import summary_stats


def test_cagr_uses_wealth_ratio_not_final_level():
    """Equity 2.0 → 4.0 over ~1 year is a 2x wealth ratio → ~100% CAGR, NOT the
    ~300% a naive final-level reading would give."""
    dates = pd.date_range("2021-01-01", periods=366, freq="D")
    eq = pd.Series(np.linspace(2.0, 4.0, 366), index=dates)
    s = summary_stats(eq)
    assert 0.9 < s["cagr"] < 1.1


def test_maxdd_is_peak_to_trough():
    dates = pd.date_range("2021-01-01", periods=5, freq="D")
    eq = pd.Series([1.0, 1.2, 0.9, 1.0, 1.1], index=dates)  # peak 1.2, trough 0.9
    s = summary_stats(eq)
    assert abs(s["max_dd"] - (0.9 / 1.2 - 1.0)) < 1e-12     # -0.25


def test_degenerate_series_is_nan():
    eq = pd.Series([1.0], index=pd.date_range("2021-01-01", periods=1, freq="D"))
    s = summary_stats(eq)
    assert np.isnan(s["sharpe"])
