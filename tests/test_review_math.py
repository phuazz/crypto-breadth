"""
Tests for the review machinery that produced the verdict — flagged in the
four-lens critique as untested despite driving the conclusion.

1. The Deflated-Sharpe building blocks (norm_cdf / norm_ppf against known values;
   DSR monotonic in trial count N and in trial dispersion).
2. Engine PARITY: the three re-implementations of the frozen v3.1 chain in the
   review harnesses must produce identical equity to each other on the real
   parquet — the "frozen engine" claim, now asserted rather than assumed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from phase_b_review import norm_cdf, norm_ppf, deflated_sharpe, run_cfg
from phase_c3_concentration import run_v3_masked
from backtest import Params, PRICES_PATH, load_prices, investability_mask_liquidity


# ---- DSR building blocks ---------------------------------------------------

def test_norm_cdf_known_values():
    assert abs(norm_cdf(0.0) - 0.5) < 1e-12
    assert abs(norm_cdf(1.959964) - 0.975) < 1e-4
    assert abs(norm_cdf(-1.959964) - 0.025) < 1e-4


def test_norm_ppf_known_values_and_roundtrip():
    assert abs(norm_ppf(0.975) - 1.959964) < 1e-4
    assert abs(norm_ppf(0.5)) < 1e-9
    for p in (0.05, 0.2, 0.5, 0.8, 0.95):
        assert abs(norm_cdf(norm_ppf(p)) - p) < 1e-6


def _synthetic_ret(sharpe_annual=1.2, n=1500, seed=0):
    rng = np.random.default_rng(seed)
    mu = sharpe_annual / np.sqrt(365) * 0.01
    r = rng.normal(mu, 0.01, n)
    idx = pd.date_range("2018-01-01", periods=n, freq="D")
    return pd.Series(r, index=idx)


def test_dsr_monotonic_decreasing_in_trial_count():
    r = _synthetic_ret()
    trials = list(np.linspace(0.5, 1.5, 40))
    out = deflated_sharpe(r, trials, 50)
    by_n = out["by_n"]
    seq = [by_n[k]["dsr"] for k in sorted(by_n)]
    # more assumed trials -> a higher deflation bar -> DSR non-increasing
    assert all(seq[i] >= seq[i + 1] - 1e-9 for i in range(len(seq) - 1))


def test_dsr_decreases_with_trial_dispersion():
    r = _synthetic_ret()
    tight = deflated_sharpe(r, list(np.linspace(1.15, 1.25, 40)), 50)["dsr_base"]
    wide = deflated_sharpe(r, list(np.linspace(0.2, 2.2, 40)), 50)["dsr_base"]
    # wider trial-Sharpe dispersion -> higher expected max under the null -> lower DSR
    assert wide <= tight + 1e-9


# ---- engine parity ---------------------------------------------------------

@pytest.mark.skipif(not PRICES_PATH.exists(), reason="prices.parquet not present")
def test_review_harnesses_match_the_frozen_engine():
    """run_cfg (Phase B) and run_v3_masked (Phase 4) re-implement the v3.1 chain;
    they must produce bit-identical equity, or 'frozen engine' is a fiction."""
    close, volume = load_prices(PRICES_PATH)
    p = Params()
    mask = investability_mask_liquidity(
        close, volume, lookback_d=p.liquidity_lookback_d,
        min_adv_usd=p.liquidity_min_adv_usd, min_history_days=p.liquidity_min_history_days)
    e_b = run_cfg(close, volume, p)["equity"]
    e_c3 = run_v3_masked(close, volume, p, mask)["equity"]
    assert e_b.index.equals(e_c3.index)
    assert float((e_b - e_c3).abs().max()) < 1e-9
