# Phase 4 — C.3 concentration (the MODIFY path) — 2026-07-04

Pre-registered: `RESEARCH_MEMO.md` PR-3. Reproducible via
`scripts/research/phase_c3_concentration.py` → `results/phase_c3_concentration.json`.
Deployment gate: the −30% MaxDD ceiling that v3.1 and every C.2 overlay failed.

## Verdict: **C.3 FAILS — no deployable candidate. The MODIFY path does not rescue v3.1.**

Neither concentrating the cross-sectional engine (C.3a) nor a new majors time-series
engine (C.3b) produces a strategy that clears −30% MaxDD *and* beats the incumbent.
The **DEPLOYABLE set is empty**. Combined with Phase B, the honest status is: v3.1's
edge is real but **undeployable at a −30% ceiling as a long-only spot book**, and no
explored modification changes that.

## C.3a — universe-shrink (rejected)

Restricting the eligible pool to the top-K names by trailing ADV (point-in-time),
same engine, hold top-4:

| K (pool) | full Sharpe | OOS Sharpe | MaxDD | clears −30% |
|---|---|---|---|---|
| 25 (incumbent) | 1.351 | 1.429 | −44.8% | no |
| 15 | 1.127 | 1.104 | −45.2% | no |
| 10 | 0.948 | 0.829 | −51.7% | no |
| 6 | 0.944 | 0.840 | −62.9% | no |
| 4 | 0.841 | 0.664 | −55.7% | no |

Concentration **monotonically degrades both Sharpe and MaxDD** — the exact dispersion
warning raised at interview Q5. A cross-sectional ranker needs a wide pool to rank;
starving it of breadth starves the signal. The "structurally-strongest-universe" house
rule does **not** transfer to a cross-sectional crypto momentum book — the opposite
holds.

## C.3b — majors time-series-momentum engine (rejected as deployable)

A NEW engine: each coin independent in/out via its own trend (no cross-sectional
ranking), so it does not depend on alt-dispersion. Universe {BTC+ETH, top-5-by-ADV} ×
signal {MA, MA-rising, 12−1 TSMOM} × sizing {equal-weight, vol-target}. Best variants:

| variant | full Sharpe | OOS Sharpe | MaxDD | clears −30% | beats v3.1 |
|---|---|---|---|---|---|
| BTC+ETH / ma_rising-100 / vt | 0.87 | 0.59 | −50.6% | no | no |
| BTC+ETH / ma-200 / vt | 0.84 | 0.77 | −59.7% | no | no |
| BTC+ETH / tsmom / vt | 0.79 | 0.66 | −55.7% | no | no |
| top5-ADV / * | 0.13–0.54 | ≤0.44 | −77% to −100% | no | no |

Every variant is lower-Sharpe than v3.1 (1.351) and none clears −30%. The engine is
**not buggy** — it correctly holds cash 45% of the time and was 68% cash through the
2022 bear (fully cash by Nov-2022). The deep drawdowns are **genuine single-day gap
risk**: the worst DD (−74% on the ma-200 book) is the COVID crash of 2020-03-12, a
one-day ~−50% move caught while fully invested that no weekly filter can dodge (BTC
buy-hold DD −81% over the same history). The top-5-ADV variants are worse still because
the non-BTC/ETH "majors" carry their own delisting/collapse tails (one variant −100%).

## The structural finding

Across the whole study — v3.1 (−44.8%), concentrated pools (−45 to −63%), majors trend
(−50 to −74%), and the best C.2 vol-target overlay (−33.3%) — **nothing clears −30%.**
A −30% deployment ceiling is structurally incompatible with a **long-only spot** crypto
book: the asset class delivers single-day −50% gaps (COVID, LUNA, deleveraging cascades)
that periodic long-only rebalancing cannot outrun. Vol-targeting trims the tail but
cannot close it without destroying return.

## Implication

- The v3.1 edge is statistically real (Phase B: DSR pass, WF pass, cost-robust) but
  **not deployable at −30% as a long-only spot book**, and the pre-registered
  modification path (C.3) does not rescue it.
- Closing the drawdown to −30% requires structure outside the explored space: the
  **parked C.5 work** (perpetual-futures shorting / hedging — the only mechanism that
  can cut the crypto tail), or accepting a higher book-level DD tolerance with small
  portfolio sizing (a −45% book at a 5% sleeve weight is a −2.25% portfolio event).
- No dashboard numbers should be presented as a deployable strategy. v3.1 stays a
  research-grade diversifier study with an honest do-not-deploy banner.
