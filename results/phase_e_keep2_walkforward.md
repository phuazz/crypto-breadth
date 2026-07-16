# PR-5 KEEP-2 / KEEP-3 — E.2 cap c=0.34 clears PR-1

Run 2026-07-16. Harness `scripts/research/phase_e_keep2_walkforward.py` →
`results/phase_e_keep2_walkforward.json`. Closes the gap PR-5 left open: KEEP-1
(DSR) had run, KEEP-2 (full-config walk-forward) had not, so the cap could not
replace v3.1.

**Data vintage: parquet ending 2026-07-15.** PR-5 itself ran on the 2026-07-14
vintage — the daily CI refresh (`d4f666d`) landed between the two runs. Both arms
shift by an identical +0.0002 Sharpe, so nothing turns on it, but figures here are
not bit-comparable with `phase_e_concentration.md`. Vintage is now recorded in the
JSON so this cannot be ambiguous again.

## Verdict: E.2 cap c=0.34 clears PR-1 KEEP (1), (2) and (3)

| criterion | requirement | frozen v3.1 | v3.1 + cap 0.34 | result |
|---|---|---|---|---|
| KEEP-1 deflated Sharpe | significant after haircut | DSR 0.9986 | **DSR 0.9987** | PASS |
| KEEP-2 walk-forward | OOS Sharpe-loss ≤ 0.30 | +0.080 | **+0.085** | PASS |
| KEEP-3 weak-patch autopsy | regime, not decay | Sharpe 0.598 | **0.598 (identical)** | PASS |

## KEEP-2 — full-config walk-forward

Protocol is the Phase-B one verbatim (60-config grid: 4 lookbacks × 5 top_n × 3
breadth_ma; expand-window annual re-fit over anchors 2020–2026; chain each year's
OOS returns; `loss = frozen_sharpe − refit_sharpe`).

**The control validates the harness: the uncapped arm reproduces the recorded
Phase-B figure of +0.080 exactly.** That is the evidence that the capped +0.085 is
measured on the same protocol as the number on record, not a lookalike.

| | full Sharpe | MaxDD | refit Sh | frozen Sh | loss | default picked |
|---|---|---|---|---|---|---|
| frozen v3.1 (control) | 1.349 | −44.8% | 1.354 | 1.434 | **+0.080** | 5/7 |
| v3.1 + cap 0.34 | 1.359 | −39.5% | 1.372 | 1.456 | **+0.085** | 5/7 |

The cap changes the walk-forward loss by **+0.005** — noise against a 0.30
tolerance. Config stability is unchanged: both arms pick the default config in 5 of
7 anchors, and the same two early anchors (2020, 2021) prefer breadth_ma=70. The
cap does not alter which parameters the search favours, which is what one wants
from a risk overlay: it should not reach back into the signal.

## KEEP-3 — weak-patch autopsy carries over unchanged

Not merely "similar" — **the cap never binds once in 2024–25**, so the weak-patch
book is bit-identical to v3.1.

| window | baseline Sharpe | capped Sharpe | baseline ret | capped ret | cap binds |
|---|---|---|---|---|---|
| weak patch 2024–25 | 0.598 | 0.598 | +40.1% | +40.1% | **0** |
| 2024 | 0.859 | 0.859 | +38.9% | +38.9% | 0 |
| 2025 | 0.170 | 0.170 | +0.8% | +0.8% | 0 |
| full sample | 1.349 | 1.359 | +11871.9% | +11728.6% | 14 |

The Phase-B diagnosis (2024–25 is a BTC-dominance regime the strategy structurally
cedes, not mechanism decay) is therefore inherited verbatim rather than re-argued.
The cap is inert exactly where the edge is weakest, which is the correct behaviour
for a concentration guard — the weak patch was never a concentration problem.

## What the cap actually costs

Over 8.5 years it binds **14 times in 236 risk-on rebalances (5.9%)** and gives up
**143pp of cumulative return** (+11871.9% → +11728.6%, about 1.2% of the total) in
exchange for **5.3pp less drawdown** (−44.8% → −39.5%) and the elimination of every
100%-single-name book.

Read that honestly, and with PR-5's caveat attached: the drawdown figure is not a
reliable forward estimate (PR-5 showed it rests on ~2 episodes, one of them the
degenerate 2018 three-coin era). The defensible claim remains the one PR-5 made —
**the cap is cheap insurance against a rare but catastrophic tail, not an edge
improvement.** KEEP-2 and KEEP-3 add that the insurance is also structurally
harmless: it does not move the walk-forward, does not shift config selection, and
does not touch the weak patch.

## Status

E.2 cap c=0.34 is now **eligible** to replace v3.1 under PR-1. It is **not yet
adopted** — adoption is an engine change (v3.1 → v3.2) with cross-cutting
consequences (the live dashboard's headline MaxDD would move −44.8% → −39.5%, which
every filed v3.1 record and the dashboard banner still quote). That requires
explicit owner sign-off and a versioning plan that keeps the filed v3.1 records
reproducible. Until then **v3.1 remains the production baseline** and this is a
completed validation, not a modification.

No new trials: KEEP-2 re-evaluates the already-registered E.2 c=0.34 config under
the Phase-B protocol and selects nothing from the grid. The v3.1 pool stays at 84.
(An adversarial reading is that the 60-config capped grid is 60 further trials; the
conclusion is insensitive either way, since DSR ≥ 0.9979 out to N=200.)
