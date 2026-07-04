# Phase B — robustness review of v3.1 (2026-07-04)

Pre-registered rule: `RESEARCH_MEMO.md` PR-1 (frozen 2026-07-04, before any data
work). Reproducible via `scripts/research/phase_b_review.py` →
`results/phase_b_review.json`; trials logged in `results/trial_registry.jsonl`.
Substrate carries the Phase-2 survivorship caveat (headline = upper bound).

## Verdict (amended 2026-07-04): **KEEP — deployable as a small crypto sleeve.**

> **Amendment note.** The −30% MaxDD ceiling was REMOVED post-hoc at owner instruction —
> it imported an equity-style drawdown frame onto crypto (passive crypto draws −81/−85%).
> Logged transparently for pre-registration integrity (RESEARCH_MEMO PR-1 amendment).
> **Under the ORIGINAL frozen −30% rule the verdict was MODIFY / do-not-deploy; the
> analysis below is unchanged — only the deploy/no-deploy gate moved.**

**Amended verdict: KEEP.** The edge clears all three KEEP criteria (DSR, WF,
regime-not-decay) AND beats passive crypto (v3.1 Sharpe 1.35 / MaxDD −44.8% vs BTC 0.61 /
−81%; 60/40 0.66 / −85%) — deployable as a **small** crypto diversifier sleeve. The
caveats now govern SIZING, not the gate: the headline is survivorship-optimistic (upper
bound), 2021-concentrated (+1882% one year), and on-notice from the 2025-26 weak patch;
expect −45%+ drawdowns (shallower than passive crypto, deep in absolute terms). The
pre-registered C.3 modification path still failed (Phase 4), so v3.1 itself is the
candidate; the C.2 vol-target overlay is optional tail-trimming (−44.8% → ~−33%).

**Original verdict (under the frozen −30% rule, for the record): MODIFY / do-not-deploy.**
The strategy failed the −30% ceiling, no overlay/concentration/majors-engine cleared it,
and the return profile is alt-dispersion-dependent and 2021-concentrated.

## Pre-registered criteria (PR-1)

| Test | Result | Pass? |
|---|---|---|
| (1) Deflated Sharpe significant over the true trial count | DSR = 0.999 at N=50 (and ≥0.999 for N=10→200); SR₀=0.28 vs annual Sharpe 1.351 | **PASS** |
| (2) Full-config walk-forward OOS Sharpe-loss ≤ 0.30 | re-fit loses **+0.080** vs frozen (1.437→1.357); default picked 5/7, (30,90,180)+top-4 picked 7/7 | **PASS** |
| (3) Weak patch = regime, not mechanism decay | score has NOT inverted (pick-vs-field spread +0.15%, still positive); new ATH Dec-2024; gate de-risking (exp 0.40 vs 0.63) | **PASS (regime), on notice** |
| Hard MaxDD deploy ceiling −30% | −44.8% as-is; **no** grid config and **no** vol-target overlay clears −30% | **FAIL** |

KEEP requires (1)+(2)+(3) *and* deployability. (1)–(3) hold, but the ceiling fails and
is not overlay-rescuable → not KEEP; the edge is real → not RETIRE → **MODIFY**.

## Evidence

**1. Deflated Sharpe.** Annual Sharpe 1.351 over T=3105 daily obs, skew +1.27, kurt
20.5. Trial-Sharpe dispersion (60-config grid) SD=0.124 annual. Even at N=200 trials,
SR₀=0.344 and DSR=0.999. The edge is far above the multiple-testing noise floor — it is
**not** a trial-count artefact. (This is orthogonal to the Phase-2 survivorship bias,
which concerns the *level*, not the *significance*.)

**2. Full-config walk-forward.** A 60-config factorial (lookbacks × top-N × breadth-MA)
re-selected annually on an expanding IS window and chained OOS scores Sharpe 1.357 vs
the frozen 1.437 — re-fitting *loses* +0.080, well inside the 0.30 tolerance. (30,90,180)
+ top-4 was IS-best in all seven anchors; only breadth-MA wavered (70 in 2020–21, 50
after). The parameters are not overfit.

**3. Cost / execution stress.** Full Sharpe 1.351 (10bps) → 1.315 (20) → 1.279 (30) →
1.206 (50bps); OOS 1.429 → 1.280. The edge survives realistic taker-level costs.

**4. Weak-patch autopsy (C.1).** The all-time peak was **214× on 2024-12-02**; the
strategy then drew down **−44.8% into 2026-06** (currently −44.2%). It made a *new* ATH
in Dec-2024, so this is not "broken since 2021." In the weak patch the breadth gate is
actively de-risking (avg gross exposure 0.40 vs 0.63 in a good period; 42% in cash vs
26%), and the momentum score has **not** inverted (pick-vs-field forward-5d spread stays
+0.15%). BUT the edge has collapsed ~85% (good-period spread +1.02%, hit-rate 42% →
weak +0.15%, 26%): the strategy needs alt-dispersion / fat-right-tail alt winners, and a
BTC-dominated 2025–26 offered none. **Diagnosis: severe regime episode, not signal
inversion — but on notice.**

**5. Return concentration.** Annual: 2018 −26%, 2019 +145%, 2020 +103%, **2021 +1882%**,
2022 −24%, 2023 +84%, 2024 +48%, 2025 +0.8%, 2026 YTD −22%. The headline CAGR is
overwhelmingly the single 2021 alt-blowoff — which is *also* the most
survivorship-inflated year (the 40 missing dead alts bite hardest in the blow-off).
Strip 2021 and the deployable edge is far more pedestrian.

**6. C.2 vol-target overlay (registered arm).** The risk-overlay-lab round-1 winner
(EWMA, band 0.10, cap 1.0, weekly) transferred onto the crypto book does **not** clear
the ceiling at any target: 30%→−33.3% (Sharpe 1.04), 40%→−34.4%, 50%→−33.4%,
60%→−35.6%, 80%→−38.3%. The equity-book overlay does not transfer cleanly because the
crypto tail (kurt 20.5, gap-down days) outruns a lagged vol estimate. Overlay = partial
mitigant, not a fix.

## Trial-count reconstruction (for the DSR)

Compact search: repo starts at v3 (`ff0adff`); the v0/v1 ladder lives in
`scripts/research/`. Distinct configurations whose performance was observed:
sensitivity OAT ~33 + vol-target grid 12 + walk-forward grid 6 + structural ladder
(v0 fixed-10 / v1 trend / v1 vol-target / v2 daily-exit / v3 liquidity) ~5, net of
overlaps ≈ **50**. DSR is robust to this estimate (≥0.999 for N=10→200).

## The modification path (→ Phase 4, pre-registered PR-3)

The named, fixable flaw is **structural dependence on alt-dispersion regimes**, which
drives both the −44% drawdowns and the 2021 concentration. The overlay cannot fix a
mechanism problem. The pre-registered C.3 work targets it directly:
- **C.3a** — universe concentration on the same engine (does a tighter pool improve the
  risk profile? note: no grid config cleared −30%, so expectations are modest).
- **C.3b** — a **new majors time-series-momentum engine** that does not need
  cross-sectional alt-dispersion and would hold BTC/ETH in BTC-led regimes rather than
  ceding them. This is the lead candidate to *replace or complement* v3.1 for deployment.

## Deployment status

**Do NOT deploy v3.1 as-is.** Edge confirmed real and cost-robust, but MaxDD −44.8%
fails the −30% ceiling, the overlay does not rescue it, and the headline is
survivorship- and 2021-inflated. Deployment (if any) awaits a C.3 modification that
clears the ceiling on its own merits.
