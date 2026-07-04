"""
Breadth-gate tiering and point-in-time investability.

The gate maps breadth % to a graduated gross-exposure tier; a NaN breadth (early
sample, MA undefined) must force cash. The liquidity gate must require BOTH a
minimum history length AND a minimum trailing ADV — the survivorship-bias fix.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest import breadth_to_tier, investability_mask_liquidity, Params


def test_breadth_to_tier_boundaries():
    """Thresholds (0.30, 0.50, 0.70) → exposures (0, 0.30, 0.60, 1.00).

    The mapping is lower-edge-inclusive: breadth == a threshold lands in the
    HIGHER tier (0.30 → 0.30 exposure, 0.50 → 0.60, 0.70 → 1.00)."""
    p = Params()
    idx = pd.date_range("2021-01-01", periods=6, freq="D")
    breadth = pd.Series([0.20, 0.30, 0.49, 0.50, 0.70, 0.90], index=idx)
    tiers = breadth_to_tier(breadth, p.tier_thresholds, p.tier_exposures)
    assert list(tiers.values) == [0.0, 0.30, 0.30, 0.60, 1.00, 1.00]


def test_breadth_to_tier_nan_forces_cash():
    """NaN breadth (MA not yet defined) → 0 exposure, not a stale carry-forward."""
    p = Params()
    idx = pd.date_range("2021-01-01", periods=2, freq="D")
    breadth = pd.Series([np.nan, 0.90], index=idx)
    tiers = breadth_to_tier(breadth, p.tier_thresholds, p.tier_exposures)
    assert tiers.iloc[0] == 0.0
    assert tiers.iloc[1] == 1.0


def test_liquidity_gate_requires_history_and_adv():
    """A coin becomes investable only once it clears the min-history bar, even
    when it is liquid from day one; a coin with too little history never does."""
    dates = pd.date_range("2021-01-01", periods=120, freq="D")
    close = pd.DataFrame(100.0, index=dates, columns=["A", "B"])
    vol = pd.DataFrame(1_000_000.0, index=dates, columns=["A", "B"])  # $100M ADV
    # B lists late: only the last 20 days exist (use .loc — pandas 3.0 CoW).
    close.loc[dates[:100], "B"] = np.nan
    vol.loc[dates[:100], "B"] = np.nan

    mask = investability_mask_liquidity(
        close, vol, lookback_d=30, min_adv_usd=25e6, min_history_days=90,
    )
    # A: 90-day history bar clears exactly on the 90th observation (row index 89).
    assert mask["A"].iloc[88] == False
    assert mask["A"].iloc[89] == True
    assert mask["A"].iloc[-1] == True
    # B: only 20 days of history → never reaches 90 → never investable.
    assert mask["B"].iloc[-1] == False
    assert mask["B"].sum() == 0


def test_liquidity_gate_rejects_thin_volume():
    """Ample history but sub-threshold ADV → never investable."""
    dates = pd.date_range("2021-01-01", periods=120, freq="D")
    close = pd.DataFrame(100.0, index=dates, columns=["A"])
    vol = pd.DataFrame(1_000.0, index=dates, columns=["A"])  # $0.1M ADV << $25M
    mask = investability_mask_liquidity(
        close, vol, lookback_d=30, min_adv_usd=25e6, min_history_days=90,
    )
    assert mask["A"].sum() == 0
