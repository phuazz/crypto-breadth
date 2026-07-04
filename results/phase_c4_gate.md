# Phase 5 — C.4 gate ablation — 2026-07-04

Pre-registered: `RESEARCH_MEMO.md` PR-4. Reproducible via
`scripts/research/phase_c4_gate.py` → `results/phase_c4_gate.json`. Same universe,
momentum ranking and top-N hold; only the gross-exposure gate changes.

## Verdict: **the breadth gate is VALIDATED** — it earns its complexity vs a BTC-200d filter and vs no gate. Two caveats; no change to deployability.

| gate | full Sharpe | full MaxDD | OOS Sharpe | weak-24+ Sharpe | weak-24+ MaxDD |
|---|---|---|---|---|---|
| **breadth (incumbent)** | **1.351** | **−44.8%** | 1.429 | 0.29 | −44.8% |
| breadth_binary (0,0,0,1) | 1.319 | −45.4% | 1.412 | 0.38 | −45.4% |
| btc200 (BTC>200d) | 1.178 | −51.2% | 1.324 | 0.43 | −48.3% |
| btc50_200 (BTC>200d & >50d) | 1.295 | −46.1% | **1.533** | 0.40 | −46.1% |
| none (always invested) | 0.742 | −82.9% | 0.840 | 0.01 | −71.2% |

## Findings

1. **The gate is load-bearing.** Removing it (`none`) collapses Sharpe by **−0.609**
   (1.351 → 0.742) and blows MaxDD out by **+38.2pp** (−44.8% → −82.9%). This is the
   single most important component of the strategy — larger than the README's stated
   +0.25 Sharpe / +30pp. Whatever else is true of v3.1, the gate is real.

2. **Breadth beats a simple BTC-200d filter, full-sample** — +0.173 Sharpe (1.351 vs
   1.178) and a shallower MaxDD (−44.8% vs −51.2%). The breadth read carries
   information a single-asset BTC trend filter does not. It earns its complexity.

3. **But the tiering is redundant.** Binary breadth (0,0,0,1) scores 1.319 / −45.4% —
   within noise of the tiered gate (1.351 / −44.8%). The 0/30/60/100% graduation adds
   nothing; a binary on/off breadth gate would do the same job. (README claim
   confirmed.)

4. **Regime caveat — the edge has narrowed recently.** In the post-2024 weak patch the
   BTC-based gates actually *edged* the breadth gate (btc200 0.43, btc50_200 0.40 vs
   breadth 0.29 Sharpe), and btc50_200 posts the best OOS Sharpe overall (1.533). The
   breadth gate's superiority is not uniform across regimes and has thinned in the
   current BTC-dominated stretch.

## Bearing on the review

The gate ablation confirms the strategy's architecture is sound where it matters most
(the gate), and the breadth read is the right gate. But this does **not** change the
Phase-B / Phase-4 conclusion: every gate variant still fails the −30% MaxDD ceiling
(best −44.8%). A better gate cannot fix a long-only-spot tail problem. v3.1 remains a
validated research diversifier, not a deployable book at −30%.
