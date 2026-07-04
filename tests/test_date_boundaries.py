"""
UTC calendar / date-boundary handling (house rule: at least one month boundary
and one year boundary, weekdays never computed from memory — pandas is the
oracle here).

The weekly rebalance is keyed off DatetimeIndex.weekday; the passive benchmarks
rebalance on is_month_start. Both must behave correctly across a month boundary
and a year boundary.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest import build_target_weights, benchmark_60_40_btc_eth


def test_weekly_rebalance_across_month_boundary():
    """Jan → Feb 2021. Rebalance rows must be exactly the Mondays, including the
    month-boundary Monday (2021-02-01)."""
    dates = pd.date_range("2021-01-25", "2021-02-08", freq="D")
    rank = pd.DataFrame(0.25, index=dates, columns=list("ABCD"))
    exposure = pd.Series(1.0, index=dates)

    tw = build_target_weights(rank, exposure, rebalance_weekday=0)
    rebal_rows = list(tw.dropna(how="all").index)

    assert rebal_rows == [d for d in dates if d.weekday() == 0]
    assert pd.Timestamp("2021-02-01") in rebal_rows        # boundary Monday
    # Non-rebalance rows are entirely NaN (forward-filled later in the loop).
    for d in dates:
        if d.weekday() != 0:
            assert tw.loc[d].isna().all()


def test_weekly_rebalance_across_year_boundary():
    """Dec 2020 → Jan 2021. The first Monday of the new year (2021-01-04) must be
    a rebalance row."""
    dates = pd.date_range("2020-12-28", "2021-01-11", freq="D")
    rank = pd.DataFrame(0.25, index=dates, columns=list("ABCD"))
    exposure = pd.Series(1.0, index=dates)

    tw = build_target_weights(rank, exposure, rebalance_weekday=0)
    rebal_rows = list(tw.dropna(how="all").index)

    assert rebal_rows == [d for d in dates if d.weekday() == 0]
    assert pd.Timestamp("2021-01-04") in rebal_rows        # first Monday of 2021


def test_monthly_benchmark_rebalances_on_year_boundary():
    """The 60/40 benchmark keys rebals off is_month_start; 2021-01-01 must be the
    only month-start in a Dec→Jan window, and equity stays finite across it."""
    dates = pd.date_range("2020-12-28", "2021-01-04", freq="D")
    close = pd.DataFrame({"BTC": 100.0, "ETH": 100.0}, index=dates)

    eq = benchmark_60_40_btc_eth(close)
    month_starts = [d for d in dates if d.is_month_start]

    assert month_starts == [pd.Timestamp("2021-01-01")]
    assert eq.notna().all()
    assert len(eq) == len(dates)
