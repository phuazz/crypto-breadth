"""
Dashboard / digest "target allocation if rebalanced now" must match the engine.

pipeline.intended_per_coin_weight feeds both the dashboard walkthrough and the
email digest's "Target if rebalanced now" line. It is display code, but it is
forward GUIDANCE — if it disagrees with backtest.rank_top_n, the reader is told
the strategy will do something it will not.

The bug this pins (2026-07-14 digest): the weight was divided by top_n rather
than by the number of names actually selected, so a thin eligible set displayed
7.5% each / 85% cash against the engine's true 15% each / 70% cash — a 2x
understatement of intended deployment. The engine deploys the FULL tier exposure
across whatever is eligible, concentrating rather than under-deploying; that
distinction only shows up when len(selected) < top_n, which is why filling all
four slots hid it.

These tests assert parity against rank_top_n itself, not against hard-coded
numbers, so a future change to the engine's sizing cannot silently desync.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest import (Params, investability_mask_liquidity, breadth_pct_above_ma,
                      breadth_to_tier, momentum_score, per_coin_trend_entry_mask,
                      rank_top_n)
from pipeline import intended_per_coin_weight, signal_walkthrough


def _engine_per_coin_weight(scores: dict, top_n: int, exposure: float) -> float:
    """What the engine actually deploys per held name: rank_top_n × exposure.

    `scores` maps coin -> composite momentum score, with NaN for names that
    failed the trend filter (mirroring `mom.where(entry_trend)` in backtest.main).
    """
    idx = pd.date_range("2026-07-13", periods=1, freq="D")  # a Monday; rebalance day
    score = pd.DataFrame([scores], index=idx)
    w = rank_top_n(score, top_n) * exposure
    held = w.loc[idx[0]]
    held = held[held > 0]
    return float(held.iloc[0]) if len(held) else 0.0


@pytest.mark.parametrize("n_eligible", [1, 2, 3, 4, 5, 8])
def test_matches_engine_for_any_eligible_count(n_eligible):
    """Parity with rank_top_n across thin, exact and over-full eligible sets."""
    p = Params()
    exposure = 0.30
    # n_eligible names carry a real score; the rest failed the trend filter (NaN).
    scores = {f"C{i}": float(10 - i) for i in range(n_eligible)}
    scores.update({f"X{j}": np.nan for j in range(3)})

    n_selected = min(p.rank_top_n, n_eligible)
    ours = intended_per_coin_weight(exposure, n_selected)
    theirs = _engine_per_coin_weight(scores, p.rank_top_n, exposure)
    assert ours == pytest.approx(theirs), (
        f"{n_eligible} eligible: display {ours:.4f} vs engine {theirs:.4f}")


def test_thin_eligible_set_deploys_full_tier():
    """The regression case: 2 eligible at the 30% tier is 15% each / 70% cash —
    the full tier, concentrated — NOT 7.5% each / 85% cash."""
    w = intended_per_coin_weight(0.30, 2)
    assert w == pytest.approx(0.15)
    assert w * 2 == pytest.approx(0.30)  # full tier deployed, not half


def test_full_slate_unchanged():
    """4 eligible at the 30% tier: 7.5% each. This case was always correct and
    must stay so — it is why the bug went unnoticed."""
    assert intended_per_coin_weight(0.30, 4) == pytest.approx(0.075)


def test_cash_gate_deploys_nothing():
    """Exposure 0 (breadth below the 30% gate) → zero weight regardless of how
    many names are eligible."""
    assert intended_per_coin_weight(0.0, 2) == 0.0


def test_no_selection_is_zero_not_error():
    """No eligible names → 0.0, and no ZeroDivisionError."""
    assert intended_per_coin_weight(0.30, 0) == 0.0


def test_missing_exposure_is_zero_not_error():
    """A None exposure (NaN breadth early in the sample) → 0.0, not a crash."""
    assert intended_per_coin_weight(None, 2) == 0.0


# ---- integration: the helper is only half the story -------------------------
# The tests above compute n_selected the way the FIXED code does, so they cannot
# catch a bad `selected` list. This drives the real signal_walkthrough against the
# real engine on a panel built to trip the specific divergence.

def _panel_with_a_nan_score_coin():
    """3 long-history coins + 1 with only 120d of history.

    The short-history coin clears the liquidity gate (90d history) and the trend
    filter (50d MA, rising) but its 180d momentum lookback is still undefined, so
    its composite score is NaN. backtest.rank_top_n drops it via row.dropna();
    pipeline's step3_rows RETAINS it (sorted to the bottom). Slicing step3_rows
    directly therefore put a phantom 4th name into `selected` and divided the tier
    by 4 instead of 3.
    """
    n = 400
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    close = pd.DataFrame(index=idx)
    for i, c in enumerate(["AAA", "BBB", "CCC"]):
        close[c] = np.linspace(100, 300 + i * 10, n)
    new = np.full(n, np.nan)
    new[-120:] = np.linspace(50, 90, 120)
    close["NEW"] = new
    volume = pd.DataFrame(1_000_000.0, index=idx, columns=close.columns)
    volume[close.isna()] = np.nan
    return close, volume


def test_nan_score_coin_is_never_shown_as_a_target():
    """The engine will never hold a NaN-score name, so the dashboard must not
    advertise it — and the per-coin weight must divide by the 3 names actually
    rankable, not by top_n."""
    p = Params()
    close, volume = _panel_with_a_nan_score_coin()
    mask = investability_mask_liquidity(
        close, volume, lookback_d=p.liquidity_lookback_d,
        min_adv_usd=p.liquidity_min_adv_usd,
        min_history_days=p.liquidity_min_history_days)
    breadth = breadth_pct_above_ma(close, p.breadth_ma_window, mask)
    texp = breadth_to_tier(breadth, p.tier_thresholds, p.tier_exposures)
    last = close.index[-1]

    # Precondition: NEW must actually reach step 2 with a NaN score, else this
    # test is vacuous and would pass against the buggy code too.
    et = per_coin_trend_entry_mask(close, p.per_coin_trend_window)
    mom = momentum_score(close, p.momentum_lookbacks_d, mask).where(et)
    assert bool(mask.loc[last, "NEW"]) and bool(et.loc[last, "NEW"])
    assert pd.isna(mom.loc[last, "NEW"])

    # Engine truth: rank_top_n drops NEW; note .mul(axis=0) — the engine aligns the
    # exposure Series on the DATE index, not on columns.
    engine = rank_top_n(mom, p.rank_top_n).mul(texp, axis=0).loc[last]
    engine = engine[engine > 0]

    wt = signal_walkthrough(close, volume, breadth, texp, mask, p)
    shown = {h["coin"]: h["weight"] for h in wt["final"]["holdings"]}

    assert "NEW" not in wt["step4"]["selected"], "phantom target the engine cannot hold"
    assert set(shown) == set(engine.index)
    for c, w in shown.items():
        assert w == pytest.approx(float(engine[c])), f"{c}: display {w} vs engine {engine[c]}"
    assert sum(shown.values()) == pytest.approx(float(engine.sum()))
