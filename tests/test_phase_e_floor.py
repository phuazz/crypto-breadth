"""
Phase-E concentration-floor path — no-look-ahead + parity (PR-5 guard 3).

PR-5 guard 3 requires that `n_eligible` is computed from the same close-T
information as the rest of the signal and traded T+1, asserted BEFORE any metric
is read. These tests run before the harness.

Also pins the two ways the arms could silently misrepresent the engine:
  - build_tw must equal the frozen build_target_weights when uncapped, or the
    E.2 re-implementation has drifted from v3.1;
  - each arm must do what PR-5 says (floor to cash / cap per name / pro-rata),
    since a mislabelled arm would corrupt the trial record.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest import (Params, build_target_weights, rank_top_n, run_backtest,
                      momentum_score)
from phase_e_concentration import arm_weights, build_tw, eligible_count


def _panel(n=8, cols=("A", "B", "C", "D", "E")):
    # 2021-01-04 is a Monday — the rebalance weekday.
    idx = pd.date_range("2021-01-04", periods=n, freq="D")
    return pd.DataFrame(100.0, index=idx, columns=list(cols))


def _ranks_gate(n=8, n_elig=2, tier=0.30):
    close = _panel(n)
    idx = close.index
    score = pd.DataFrame(np.nan, index=idx, columns=close.columns)
    for i, c in enumerate(list(close.columns)[:n_elig]):
        score[c] = 10.0 - i          # only the first n_elig names are rankable
    ranks = rank_top_n(score, Params().rank_top_n)
    gate = pd.Series(tier, index=idx)
    return close, score, ranks, gate


def test_build_tw_matches_frozen_builder_when_uncapped():
    """The E.2 path re-implements the gate multiply; uncapped it must be
    bit-identical to the frozen engine, else v3.1 has drifted."""
    _, _, ranks, gate = _ranks_gate()
    p = Params()
    assert build_tw(ranks, gate, p.rebalance_weekday).equals(
        build_target_weights(ranks, gate, p.rebalance_weekday))


def test_eligible_count_uses_only_close_t():
    """n_eligible is a per-row count of non-NaN scores — no shift, no leakage of
    a future row into the current one. Changing a LATER row must not change an
    EARLIER count."""
    _, score, _, _ = _ranks_gate(n_elig=2)
    before = eligible_count(score).copy()
    score.iloc[-1, :] = 1.0                      # perturb only the last row
    after = eligible_count(score)
    pd.testing.assert_series_equal(before.iloc[:-1], after.iloc[:-1])


def test_floor_target_still_realised_next_bar():
    """The floored path inherits lag_days=1: a target on row T is realised at
    T+1, never same-bar. This is the guard-3 assertion."""
    close, _, ranks, gate = _ranks_gate(n=8, n_elig=4)
    p = Params()
    n_elig = pd.Series(4, index=close.index)
    tw = arm_weights("E1", ranks, gate, n_elig, p, k=2)
    res = run_backtest(close, tw, fee_bps_per_side=0.0, lag_days=1)
    w = res["weights"]
    mondays = [d for d in close.index if d.weekday() == p.rebalance_weekday]
    d0 = mondays[0]
    i = close.index.get_loc(d0)
    assert w.loc[d0].sum() == 0.0                       # not traded on the signal bar
    assert abs(w.iloc[i + 1].sum() - 0.30) < 1e-9       # traded the next bar


def test_e1_floor_goes_to_cash_below_k():
    """k=3 with only 2 eligible → cash. k=2 with 2 eligible → deployed."""
    close, _, ranks, gate = _ranks_gate(n_elig=2)
    p = Params()
    n_elig = eligible_count(_ranks_gate(n_elig=2)[1])
    blocked = arm_weights("E1", ranks, gate, n_elig, p, k=3).dropna(how="all")
    allowed = arm_weights("E1", ranks, gate, n_elig, p, k=2).dropna(how="all")
    assert blocked.sum(axis=1).max() == 0.0
    assert abs(allowed.sum(axis=1).max() - 0.30) < 1e-9


def test_e2_cap_binds_and_residual_is_cash():
    """2 eligible at the 30% tier is 15% each uncapped. A 0.34 cap does not bind;
    a 0.10 cap binds and the residual must fall to cash (gross 20%, not 30%)."""
    close, _, ranks, gate = _ranks_gate(n_elig=2)
    p = Params()
    ne = pd.Series(2, index=close.index)
    loose = arm_weights("E2", ranks, gate, ne, p, c=0.34).dropna(how="all")
    tight = arm_weights("E2", ranks, gate, ne, p, c=0.10).dropna(how="all")
    assert abs(loose.max(axis=1).max() - 0.15) < 1e-9      # cap does not bind
    assert abs(loose.sum(axis=1).max() - 0.30) < 1e-9      # full tier deployed
    assert abs(tight.max(axis=1).max() - 0.10) < 1e-9      # cap binds
    assert abs(tight.sum(axis=1).max() - 0.20) < 1e-9      # residual to cash


def test_e3_prorata_underdeploys_when_thin_and_matches_baseline_when_full():
    """E.3 gives every name tier/top_n. With 2 of 4 slots filled at the 30% tier
    that is 7.5% each (15% gross, half the tier). With all 4 filled it must equal
    the frozen baseline exactly."""
    p = Params()
    close, _, ranks2, gate2 = _ranks_gate(n_elig=2)
    ne2 = pd.Series(2, index=close.index)
    thin = arm_weights("E3", ranks2, gate2, ne2, p).dropna(how="all")
    assert abs(thin.max(axis=1).max() - 0.075) < 1e-9
    assert abs(thin.sum(axis=1).max() - 0.15) < 1e-9

    _, _, ranks4, gate4 = _ranks_gate(n_elig=4)
    ne4 = pd.Series(4, index=close.index)
    full = arm_weights("E3", ranks4, gate4, ne4, p)
    base = arm_weights("baseline", ranks4, gate4, ne4, p)
    pd.testing.assert_frame_equal(full, base)
