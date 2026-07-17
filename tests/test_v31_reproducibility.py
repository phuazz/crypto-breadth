"""
v3.1 reproducibility under the v3.2 engine (adopted 2026-07-16, PR-5 arm E.2).

The whole versioning design rests on one property: `build_target_weights` must
behave EXACTLY as v3.1 did unless a caller explicitly passes `single_name_cap`.
That is what lets the scripts/research/phase_*.py harnesses keep reproducing the
FILED review records (Sharpe 1.35, MaxDD -44.8%, the DSR, the C.3a/C.4 ablations)
after the production default moved to 0.34.

If these tests fail, every filed v3.1 record silently became unreproducible.

They also pin the converse: Params carries the v3.2 default, so a production path
that forwards p.single_name_cap gets the cap. Both halves matter — the first
protects the record, the second makes the adoption real.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest import Params, build_target_weights, rank_top_n


def _ranks_gate(n_elig=2, tier=1.00, top_n=4):
    idx = pd.date_range("2021-01-04", periods=3, freq="D")  # Monday start
    cols = [f"C{i}" for i in range(6)]
    score = pd.DataFrame(np.nan, index=idx, columns=cols)
    for i in range(n_elig):
        score[f"C{i}"] = 10.0 - i
    return rank_top_n(score, top_n), pd.Series(tier, index=idx)


def test_omitting_the_cap_is_v31_bit_for_bit():
    """The default path is unchanged. This is the guarantee the filed records
    depend on — every phase_* harness omits the argument."""
    ranks, gate = _ranks_gate(n_elig=1, tier=1.00)
    without = build_target_weights(ranks, gate, 0)
    explicit_none = build_target_weights(ranks, gate, 0, single_name_cap=None)
    pd.testing.assert_frame_equal(without, explicit_none)
    # 1 name at the 100% tier is the most extreme case: v3.1 deploys the whole book.
    assert without.dropna(how="all").max(axis=1).max() == pytest.approx(1.00)


def test_params_default_is_the_v32_cap():
    """Adoption is real: production paths forward this value."""
    assert Params().single_name_cap == pytest.approx(0.34)


def test_cap_binds_only_in_the_thin_corner():
    """1 name at the 100% tier: 100% -> 34%, residual to cash. The cap must bite
    exactly here, which is the case that motivated PR-5 (NEAR, 2026-03-16)."""
    ranks, gate = _ranks_gate(n_elig=1, tier=1.00)
    tw = build_target_weights(ranks, gate, 0, single_name_cap=0.34).dropna(how="all")
    assert tw.max(axis=1).max() == pytest.approx(0.34)
    assert tw.sum(axis=1).max() == pytest.approx(0.34)   # residual is cash, not redistributed


def test_cap_is_inert_on_an_ordinary_book():
    """4 names at the 30% tier is 7.5% each — nowhere near the cap. v3.1 and v3.2
    must be identical, i.e. the guard does not tax the normal case."""
    ranks, gate = _ranks_gate(n_elig=4, tier=0.30)
    uncapped = build_target_weights(ranks, gate, 0)
    capped = build_target_weights(ranks, gate, 0, single_name_cap=0.34)
    pd.testing.assert_frame_equal(uncapped, capped)


def test_cap_never_redistributes_to_other_names():
    """The clipped residual must fall to CASH, never be handed to the survivors —
    that would defeat the guard by re-concentrating the book elsewhere."""
    ranks, gate = _ranks_gate(n_elig=2, tier=1.00)
    tw = build_target_weights(ranks, gate, 0, single_name_cap=0.34).dropna(how="all")
    row = tw.iloc[0]
    held = row[row > 0]
    assert len(held) == 2
    assert all(h == pytest.approx(0.34) for h in held)
    assert row.sum() == pytest.approx(0.68)   # NOT 1.00


def test_production_paths_all_pass_the_cap():
    """The CONVERSE guard, and the important one.

    build_target_weights defaults to None, which protects the filed records but is
    FAIL-OPEN for production: a new production path (or a regression dropping the
    kwarg) silently reverts to v3.1 uncapped — the exact tail the cap exists to
    close — and no test fails. Verified: removing the kwarg from pipeline.run_v3
    left all 98 tests green.

    So assert it statically. Every module in scripts/ (not scripts/research/,
    which is the v3.1 record layer) that calls build_target_weights must pass
    single_name_cap.
    """
    from pathlib import Path
    scripts = Path(__file__).resolve().parent.parent / "scripts"
    offenders = []
    for f in sorted(scripts.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        # Only the CALL sites matter, not the def in backtest.py.
        for i, line in enumerate(src.splitlines(), 1):
            if "build_target_weights(" not in line or line.lstrip().startswith("def "):
                continue
            # The call may wrap; look at the next couple of lines too.
            window = "\n".join(src.splitlines()[i - 1:i + 2])
            if "single_name_cap" not in window:
                offenders.append(f"{f.name}:{i}")
    assert not offenders, (
        f"production call site(s) of build_target_weights do not pass "
        f"single_name_cap and therefore silently trade v3.1 UNCAPPED: {offenders}")


def test_research_harnesses_do_not_pass_the_cap():
    """Static guard on the filed record. The phase_* harnesses reproduce the v3.1
    review; if one starts forwarding the cap, its .md record silently stops
    matching its own code. phase_e is exempt: it is the PR-5 harness and applies
    caps deliberately via its own arm_weights/build_tw."""
    from pathlib import Path
    research = Path(__file__).resolve().parent.parent / "scripts" / "research"
    offenders = []
    for f in research.glob("phase_*.py"):
        if f.name.startswith("phase_e"):
            continue
        if "single_name_cap" in f.read_text(encoding="utf-8"):
            offenders.append(f.name)
    assert not offenders, (
        f"v3.1 record harness(es) now pass the v3.2 cap and no longer reproduce "
        f"their filed records: {offenders}")
