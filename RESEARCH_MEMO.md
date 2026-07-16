# RESEARCH_MEMO.md — Crypto-Breadth uplift

Running research memo for the bte-quality uplift of the crypto breadth/momentum
strategy (v3.1). This is the process record; the authoritative scope + interview is
`C:\dev\KICKOFF_crypto-breadth-uplift.md`, and the ledger row lives in
`C:\dev\STUDIES_LEDGER.md`. House format follows `risk-overlay-lab/RESEARCH_MEMO.md`.

**Posture (2026-07-04).** Research-grade, no capital yet ("yet" ⇒ Phase B is a
prospective first-deployment gate). The uplift produces a keep / modify / retire
verdict on v3.1, then conditional new research (C.3 concentration, C.4 gate).
Data = existing Binance parquet only; no new vendor (Norgate verified irrelevant to
crypto). Project un-gated on the 24 July Platinum decision.

**Constraint honouring.** The v3.1 signal spec in `scripts/backtest.py` is FROZEN as
the review baseline for Phase B — no silent re-parameterisation. Any parameter change
is a named, pre-registered trial in the register below. Phase 1 (this scaffolding)
changed NO engine semantics.

---

## Pre-registration — frozen BEFORE any Phase-B data work (2026-07-04)

### PR-1. v3.1 survival rule (Phase B)

Objective function: **Sharpe headline + MaxDD hard co-primary**, benchmarked
risk-adjusted vs BTC HODL and 60/40 BTC/ETH. Verdict ∈ {keep, modify, retire}.

- **KEEP** iff all three hold:
  1. **Deflated Sharpe** — reconstruct the TRUE trial count (git history + the
     `scripts/research/` archive: v0 fixed-10 → v1 trend / vol-target →
     `vol_target_search` grid → top-N and lookback searches → the seven annual
     walk-forward re-fits) and apply the Bailey / López de Prado Deflated Sharpe
     Ratio; the edge must remain significant after the haircut.
  2. **Full-config walk-forward** — the proper expand-window annual re-fit (extend
     `scripts/walk_forward_refit.py`); OOS Sharpe-loss vs the frozen config must be
     **≤ 0.30**.
  3. **Weak-patch autopsy** — 2024–2025 diagnosed as a flat / regime stretch (a
     BTC-dominance regime the strategy structurally cedes), NOT mechanism decay.
     Operationalised: momentum still ranks forward winners within the eligible
     top-few, and the drawdown traces to breadth-off / BTC-dominance, not to the
     momentum score inverting.
- **MODIFY** iff the edge survives deflation but a specific, NAMED, structural flaw is
  isolated and fixable (e.g. the 2018 three-coin whipsaw via a minimum-qualifier
  floor; or the breadth gate failing to beat a BTC-200d filter → swap it). Each
  modification is itself a registered trial.
- **RETIRE** iff the deflated edge is statistically indistinguishable from zero over
  the true trial count, OR the autopsy diagnoses genuine mechanism decay.

**Hard MaxDD deployment ceiling: −30%.** Independent of Sharpe. v3.1 as-is (−42.5%)
FAILS the ceiling → deployment requires the C.2 vol-target overlay to clear it, which
is why C.2 is a registered arm, not optional.

**Numbers frozen 2026-07-04** (owner adopted the proposed defaults at spec freeze;
correction point was the freeze): −30% MaxDD ceiling; 0.30 OOS Sharpe-loss tolerance.

**AMENDMENT (2026-07-04, POST-HOC — logged after Phase B/4 results, owner instruction.**
Recorded transparently, NOT a silent rewrite, to preserve pre-registration integrity.)
The **−30% MaxDD ceiling is REMOVED.** Rationale: −30% imported an equity-style drawdown
frame onto a crypto book; the correct benchmark is passive crypto (BTC-HODL MaxDD −81%,
60/40 BTC-ETH −85%), against which v3.1's −44.8% is far shallower. Deployability is now
judged by KEEP criteria (1)–(3) PLUS "beats passive crypto (BTC-HODL, 60/40)
risk-adjusted, net of costs." Effect on the verdict: under the ORIGINAL frozen rule →
MODIFY / do-not-deploy; under the AMENDED rule → **KEEP / deployable as a small crypto
sleeve.** The 0.30 OOS Sharpe-loss tolerance is unchanged. The survivorship (optimistic
upper bound), 2021-concentration and weak-patch (on-notice) caveats are UNCHANGED and now
govern SIZING and expectations rather than the deploy/no-deploy gate.

**AMENDMENT 2 (2026-07-04, following the four-lens critique — owner: "go ahead").** The
outright *removal* above was outcome-motivated (the pre-registered rule had just
produced do-not-deploy). It is SUPERSEDED by a PRINCIPLED re-anchor: the deployment
ceiling is set to a **−50% crypto hard-stop** — shallower than passive crypto (BTC −81%,
60/40 −85%), chosen on principle rather than to clear this strategy. v3.1's −44.8%
passes, so the deploy decision is unchanged, but the governance is clean. Separately the
verdict is downgraded to **MARGINAL** (Phase-D follow-up: bull-levered edge, honest
year-block CI [0.37, 2.14], not a diversifier) — a tiny return-seeking satellite at
most, not a hedge.

### PR-2. C.2 vol-target overlay transfer (registered arm inside Phase B)

Transfer the `risk-overlay-lab` round-1 winner (EWMA-class estimator, band ≥ 0.10,
hard cap 1.0, weekly execution) onto the crypto book via the lab's artefact data
contract. Success = clears the −30% ceiling at acceptable Sharpe cost (target: roughly
halve the −42.5% MaxDD). Net of costs. Registered as trials in the register.

### PR-3. C.3 concentration (Phase 4) — TWO sub-arms, both from the outset

- **C.3a — universe-shrink, v3.1 engine FIXED.** Vary only the eligible pool:
  point-in-time top-8 / top-6 / top-4 / BTC+ETH / BTC-only by trailing ADV, versus the
  25-coin incumbent, net of costs. Tests the "structurally-strongest-universe" house
  rule for the cross-sectional engine — and will honestly show if dispersion loss
  dominates.
- **C.3b — NEW time-series-momentum / trend engine on the majors.** Each major
  independent in / out (no cross-sectional ranking): the per-coin trend primitive at a
  majors lookback + a classic 12−1-month TSMOM sign variant; equal-weight AND
  vol-target sizing. **C.3b is a new strategy family, not a modification of v3.1** → it
  carries its OWN trial ledger and its OWN success bar: beat v3.1 AND the BTC-HODL /
  60-40 benchmarks risk-adjusted net of costs, DSR-deflated over its own trial count,
  same −30% MaxDD deploy ceiling. C.3b trials do NOT inflate v3.1's deflation count,
  and vice-versa.

### PR-4. C.4 gate value (Phase 5)

Breadth gate versus a simple BTC 200-day MA on / off filter, same universe and sizing,
net of costs. Clean ablation: does the gate earn its complexity post-2024.

### PR-5. Concentration floor (post-close, pre-registered 2026-07-15)

**Status: PRE-REGISTERED, NOT RUN.** No code written, no trial executed. Frozen below
BEFORE any data work, per house rule.

**Provenance — a post-close discovery, logged honestly.** The review CLOSED 2026-07-04.
On 2026-07-15, while fixing a display bug in the dashboard / digest (which had been
halving the apparent position size and thereby masking this), an UNREGISTERED
structural property of the frozen engine was found and verified. This re-opens a
narrow, named question; it does not re-open the verdict.

**The named flaw.** `rank_top_n` assigns `1/min(n, len(valid))` and `build_target_weights`
then scales by the tier. The engine therefore deploys the FULL tier exposure across
however many names are trend-eligible: when eligibility is thin it CONCENTRATES rather
than under-deploying. Verified on the frozen baseline over 236 risk-on rebalance
Mondays: 39 held fewer than four names, 17 held exactly one, and **5 put 100% of the
book into a single coin** — 2018-10-01 BTC, 2018-10-08 BTC, 2019-02-25 BNB,
2019-10-28 XRP, 2026-03-16 NEAR.

**The mechanism is the high-tier / thin-eligibility corner.** Breadth (% above own 50d
MA) and entry eligibility (above own MA AND MA rising) are near-collinear but disagree
sharply after a fast bounce, when price jumps back above a still-falling MA. Breadth
then reads high (max tier) while almost nothing has a rising MA. On 2026-03-16 breadth
was 100% (⇒ the full 100% tier) and NEAR alone was eligible ⇒ 100% of the book into one
altcoin: maximum exposure meeting minimum diversification. It fell 18.1% in 14 days
(−20.0% to trough). The daily trend-exit is a stop, not diversification.

**Why this is in scope despite the closed verdict.** PR-1's MODIFY clause already names
"the 2018 three-coin whipsaw via a minimum-qualifier floor" as an example structural
fix — the 2018-10 BTC-only Mondays ARE that regime, so this is a continuation of the
pre-registered path, not a new invention. Materially: the −44.8% MaxDD that clears the
−50% hard-stop (PR-1 Amendment 2) was NOT measured on a single-name book. It is a
portfolio-level statistic over a book that was sometimes one alt. Sizing guidance for a
"tiny satellite" is conditioned on it.

**Arms — declared in full, up front. Each row is one v3.1-pool trial.**

- **E.1 — minimum-qualifier floor.** Deploy only if `n_eligible ≥ k`, else cash.
  `k ∈ {2, 3}`. (2 trials)
- **E.2 — per-name cap.** Cap any single name at `c` of the book; residual to cash.
  `c ∈ {0.34, 0.50}`. (2 trials)
- **E.3 — pro-rata scaling.** `gross = tier × (n_eligible / top_n)`; unfilled slots
  cash. (1 trial)

**Total: 5 trials. v3.1 pool 99 → 104. C.3b's pool is untouched.**

Note on E.3: it is the most obvious fix and the most suspect. It double-counts — breadth
and eligibility measure near-collinear things — and it destroys the tier's meaning as
gross exposure, under-deploying broadly to fix a corner. It is registered as the honest
comparator, not the expected winner. E.1 / E.2 target the corner specifically.

**Success bar — asymmetric, because this is a RISK fix, not a return fix.**

ADOPT a variant iff BOTH hold:
1. **The tail actually improves.** 100%-single-name Mondays → 0 (E.1 / E.3) or max
   single-name weight ≤ c (E.2); AND the worst 14-day drawdown CONDITIONAL on
   `n_eligible ≤ 2` improves materially.
2. **The edge is not bought with it.** Full-sample Sharpe loss ≤ **0.15** vs frozen v3.1
   (tighter than PR-1's 0.30 OOS tolerance, because this is a targeted guard and not a
   re-fit); MaxDD does not worsen; and the variant still clears the −50% hard-stop and
   PR-1 KEEP (1)–(3) after DSR deflation over the ENLARGED count (N=104).

REJECT (keep v3.1 as-is) if no variant clears both — and in that case record the
concentration property as an ACCEPTED, DOCUMENTED risk that governs SIZING, not as a
defect. A null result here is a publishable outcome, not a failed trial.

**Numbers frozen 2026-07-15, before any run:** `k ∈ {2,3}`; `c ∈ {0.34, 0.50}`;
Sharpe-loss tolerance 0.15; DSR over N=104.

**Three ways this could be silently wrong (standing house rule, per-arm):**
1. **Outcome-motivated design** — the grid was chosen AFTER watching NEAR fail. Guard:
   frozen here before any run; k / c are round and principled (k=2 "never single-name";
   k=3 "at least three of four slots"; c=0.34 "no name above a third"; c=0.50 "no name
   above half"), NOT tuned to 2026-03-16. Report ALL five arms, not the best.
2. **Selection on a 5-event tail** — five single-name Mondays out of 236 is a thin base,
   and a variant can look good merely by dodging one bad draw. Guard: report the FULL
   distribution of `n_eligible` and max-weight across all 236, plus the conditional
   statistic (`n_eligible ≤ 2`) — never just the five.
3. **Look-ahead in the floor** — `n_eligible` must be computed from the same close-T
   information as the rest of the signal and traded T+1. Guard: extend the existing
   no-look-ahead assertion in `tests/` to the new path before any metric is read.

**RESULT (run 2026-07-15 — full record: `results/phase_e_concentration.md`).**
Guards asserted first (`tests/test_phase_e_floor.py`, 6 tests); baseline reproduces the
recorded review figures exactly (Sharpe 1.349, MaxDD −44.8%, CAGR 75.2%).

**All five arms clear the pre-registered bar — and that sweep must NOT be read as five
wins.** Two findings govern the interpretation:
- **The arms are statistically indistinguishable.** Sharpe SE over 8.5y ≈ 0.342; the
  whole spread across arms is +0.003…+0.048, about **one seventh of one SE**. Ranking
  them on Sharpe would be noise-mining. Declared tied on edge.
- **The measured drawdown gain is ~two episodes, not a persistent effect.** Every arm's
  full-sample MaxDD IS its loss inside the single 2024-12-02 → 2026-06-06 window. E.1
  k=2 is IDENTICAL to baseline in 2020–2025, differing only in 2018 (−26.0% → 0.0%) and
  2026 (−22.7% → −13.0%). It binds on 17 Mondays, **13 of them in 2018–2019** when the
  median investable universe was **3 and 5 coins** — the three-coin whipsaw PR-1 already
  names. Four of the five 100%-single-name events are 2018–2019; only NEAR (2026-03-16)
  is modern.

**Verdict: the floor is CHEAP INSURANCE, not an edge improvement.** Established: the
concentration exposure is real (100% single-name, five times) and removing it costs
nothing measurable (no arm loses Sharpe beyond noise, none worsens MaxDD, all clear the
−50% hard-stop, all keep DSR ≥ 0.9986 at the v3.1 pool N=84). NOT established: any
forward drawdown benefit — the −44.8% → −37.5% is what happened in two specific
episodes, one a degenerate era. Adopt on principle, not on the point estimate.

**Recommendation: E.2 cap c=0.34** ("no single name above a third") if adopting — chosen
on governance grounds precisely BECAUSE the metrics cannot separate the arms.

**KEEP-2 / KEEP-3 CLOSED (2026-07-16, owner selected E.2 c=0.34 — full record:
`results/phase_e_keep2_walkforward.md`).** The gap PR-5 left open is now shut: the cap
clears PR-1 KEEP (1), (2) and (3).
- **KEEP-2 PASS.** Phase-B protocol verbatim (60-config grid, expand-window anchors
  2020–2026). The uncapped control **reproduces the recorded +0.080 exactly**, which is
  what makes the capped **+0.085** comparable to the number on record. The cap moves the
  walk-forward loss by +0.005 against a 0.30 tolerance, and does not change config
  selection (both arms pick the default in 5/7 anchors).
- **KEEP-3 PASS, inherited verbatim.** The cap **never binds in 2024–25**, so the
  weak-patch book is bit-identical (Sharpe 0.598, +40.1% both). The 2024–25 diagnosis
  (BTC-dominance regime, not decay) carries over unchanged — the weak patch was never a
  concentration problem.
- **Cost:** binds 14 / 236 risk-on rebalances (5.9%); gives up 143pp of cumulative
  return (+11871.9% → +11728.6%, ~1.2% of total) for 5.3pp less drawdown and the removal
  of every 100%-single-name book. PR-5's caveat still attaches — the drawdown number is
  NOT a reliable forward estimate. The defensible claim remains **cheap insurance, not
  an edge gain**; KEEP-2/3 add that it is also structurally harmless.
- **Data vintage:** KEEP-2 ran on the 2026-07-15 parquet, PR-5 on 2026-07-14 (a CI
  refresh landed between). Both arms shift +0.0002 Sharpe; nothing turns on it, but the
  two records are not bit-comparable. Vintage is now pinned in the JSON.

**Status: ELIGIBLE, NOT ADOPTED.** Adoption is an engine change (v3.1 → v3.2) whose
consequences reach the public dashboard (headline MaxDD −44.8% → −39.5%, a figure every
filed v3.1 record and the banner still quote). It needs explicit sign-off plus a
versioning plan that keeps the filed v3.1 records reproducible. **v3.1 remains the
production baseline** until then.

**Errors in this pre-registration, logged not silently fixed:** (a) the frozen "N=104"
cross-contaminates the pools — the v3.1 pool is B+C2+C3a+C4=79, so the correct figure is
**N=84**; both are reported (0.9986 vs 0.9984, materially identical). (b) No tie-break
was pre-specified; the bar assumed at most one arm would clear and five did, so the
recommendation is an explicit post-hoc judgement on principle. (c) The E.3 prediction
("expected to lose") was **wrong** — it improved on every axis and gives the tightest
diversification guarantee (max name weight 25%).

---

## Three ways each backtest could be silently wrong (standing set + guards)

House rule: state these before writing any backtest code, with a named guard for each.

1. **Look-ahead** in signal / gate / trend construction → assert the close-T →
   trade-T+1 lag in tests (`tests/`); audit the breadth gate and momentum score for
   same-bar leakage.
2. **Survivorship** in the point-in-time universe → audit that the rolling-liquidity
   reconstruction includes delisted pairs (LUNA / FTT era); if not, quantify the bias
   direction / magnitude and caveat (Phase 2). The README "removes survivorship bias"
   claim is itself under test.
3. **CryptoCompare ↔ Binance splice** → verify the review history is Binance-only;
   check for a level discontinuity at any splice point; different-USDT-price
   contamination.

Per-arm additions: data alignment (same UTC date index, same fill rule); month / year
boundary handling in the weekly rebalance; sensitivity of headline metrics to the 2018
three-coin regime and the 2021 alt-blowoff.

---

## Trial register

Append-only JSONL at `results/trial_registry.jsonl` (house format, per
`risk-overlay-lab`). One JSON object per registered config per line:

```
{"run_utc": "<ISO8601 UTC>", "arm": "B|C2|C3a|C3b|C4|E", "config_id": "<10-hex>",
 "config": { ... arm-specific params ... },
 "metrics": {"sharpe": .., "cagr": .., "max_dd": .., "worst_12m": .., "dsr": ..},
 "split": "full|is|oos|train|test", "notes": "<string>"}
```

Two SEPARATE deflation pools (do not cross-contaminate):
- **v3.1 pool** — arm B + C.2 + C.3a + C.4 + E modifications — feeds v3.1's DSR.
  **79 → 84** after PR-5 (2026-07-15).
- **C.3b pool** — the new majors engine — feeds C.3b's own DSR. 20, untouched by PR-5.

The true v3.1 trial count for the DSR haircut was reconstructed in Phase B (2026-07-04)
≈ **50** distinct configs (sensitivity OAT ~33 + vol-target grid 12 + walk-forward grid
6 + structural ladder ~5, net of overlaps). The DSR is robust to this estimate — ≥0.999
for N=10→200, and ≥0.9986 at the post-PR-5 v3.1 pool N=84. **104 trials logged** in
`results/trial_registry.jsonl` as of 2026-07-15 (B 64 + C2 5 + C3a 5 + C4 5 + E 5 = 84
in the v3.1 pool; C3b 20 in its own).

---

## Session log

- **2026-07-04 — Phase 1 (scaffolding-A).** Interview + spec freeze complete (see
  KICKOFF). Added this memo, `DATA_INTEGRITY_POLICY.md`, project `CLAUDE.md`, the
  pytest engine test suite (`tests/`), the append-only trial register
  (`results/trial_registry.jsonl`), and the "under review" dashboard banner. No engine
  semantics changed. Pre-registration PR-1…PR-4 frozen above. Owner then authorised
  all remaining phases (2→6) to run in one pass.

- **2026-07-04 — Phase 2 (survivorship & data audit).** Substrate is USABLE →
  proceeded (did not pause). Key findings (`results/survivorship_audit.md`,
  reproducible via `scripts/research/survivorship_audit.py`): (1) the 25-coin
  universe is a fixed, hindsight-selected set — no coin ever delists (`ends_early =
  []`); 40 of 40 well-known 2019–2021 Binance USDT era-majors are ABSENT, so the pool
  is < 40% of the true era-liquid set, gap concentrated in the alt-heavy years (2019,
  2021). Survivorship-selection bias direction = OPTIMISTIC; magnitude un-point-
  estimable without the full Binance listing (no-new-vendor) → recorded as a
  direction + rough magnitude, carried into PR-1 as an upper-bound caveat on every
  headline metric. (2) The two included deaths are honest, not fabricated (LUNA worst
  1-day −100% → residual ~$0.00006; FTT −75% → residual ~$0.23; both captured), and
  the $25M ADV gate keeps residual near-zero prices out of the book. (3) Splice CLEAN
  — the only ≥6-coin >25% days are real events (COVID, May-2021 crash, Oct-2025); BTC
  crosses the Binance→CryptoCompare handover with no level jump; no non-positive
  closes. (4) Interior NaN gaps (MATIC 569 = Sept-2024 POL rename, EOS 311, FTT 310,
  LUNA 17) are handled by the investability mask (NaN → excluded). README survivorship
  claims corrected in two places to match. NEXT: Phase 3 (B robustness review).

- **2026-07-04 — Phase 3 (B robustness review). VERDICT: MODIFY** (edge real, not
  deployable as-is). Full record: `results/phase_b_review.md`. PR-1 criteria: (1) DSR
  PASS decisively (0.999 at N=50, ≥0.999 for N=10→200 — the edge is not a
  multiple-testing artefact); (2) full-config walk-forward PASS (60-config annual re-fit
  loses only +0.080 OOS vs frozen; (30,90,180)+top-4 IS-best in all 7 anchors —
  params not overfit); (3) weak-patch = regime not decay, ON NOTICE (score has NOT
  inverted — pick-vs-field spread +0.15% still positive; new ATH Dec-2024; gate
  de-risking to 0.40 gross — but the edge collapsed ~85% in a BTC-dominated 2025–26).
  Cost-robust (Sharpe 1.21 at 50bps). BUT the −30% MaxDD deploy ceiling FAILS (−44.8%,
  peak Dec-2024 → trough Jun-2026), NO grid config and NO vol-target overlay clears it
  (C.2 best −33.3% at Sharpe 1.04 — crypto tail too fat, kurt 20.5), and the track is
  grotesquely 2021-concentrated (+1882% single year, the most survivorship-inflated).
  → MODIFY. The named fixable flaw = structural alt-dispersion dependence; the
  pre-registered C.3 work is the fix path, C.3b (majors TS-momentum) the lead. Do NOT
  deploy v3.1 as-is. NEXT: Phase 4 (C.3 concentration).

- **2026-07-04 — Phase 4 (C.3 concentration). VERDICT: C.3 FAILS — no deployable
  candidate; the MODIFY path does not rescue v3.1.** Full record:
  `results/phase_c3_concentration.md`; 25 trials appended to the registry (arms
  C3a/C3b). C.3a (universe-shrink) monotonically HURTS — Sharpe 1.351→0.841 as the pool
  shrinks 25→4, MaxDD worsening (the Q5 dispersion warning confirmed: a cross-sectional
  ranker needs a wide pool). C.3b (new majors TS-momentum engine, not buggy — 45% cash,
  68% cash through the 2022 bear) is lower-Sharpe than v3.1 on every variant (best 0.87)
  and NONE clears −30% (best −50.6%); deep DDs are genuine single-day gap risk (worst
  −74% = COVID 2020-03-12, a 1-day −50% move a weekly filter cannot dodge). DEPLOYABLE
  set EMPTY. STRUCTURAL FINDING: across the whole study nothing clears −30% (v3.1 −44.8%,
  concentrated −45→−63%, majors trend −50→−74%, best overlay −33.3%) — a −30% ceiling is
  incompatible with a long-only SPOT crypto book; closing it needs the parked C.5
  (perp/short hedge) or a higher DD tolerance at small sleeve sizing. NEXT: Phase 5 (C.4
  gate ablation).

- **2026-07-04 — Phase 5 (C.4 gate ablation). Breadth gate VALIDATED.** Full record:
  `results/phase_c4_gate.md`; 5 trials appended (arm C4). The gate is load-bearing —
  removing it collapses Sharpe −0.609 (1.351→0.742) and blows MaxDD +38.2pp
  (−44.8%→−82.9%), the single most important component. The breadth gate beats a simple
  BTC-200d filter full-sample (+0.173 Sharpe, shallower DD) → earns its complexity. Two
  caveats: the tiering is redundant (binary 0,0,0,1 breadth ≈ tiered, README confirmed),
  and in the post-2024 weak patch the BTC filters edged breadth (btc200 0.43 / btc50_200
  0.40 vs breadth 0.29; btc50_200 best OOS 1.533). Does NOT change deployability — every
  gate still fails −30%. NEXT: Phase 6 (cosmetic-A: dashboard conformance + CI, surface
  only surviving conclusions, banner → do-not-deploy).

- **2026-07-04 — Phase 6 (cosmetic-A: dashboard + CI).** Dashboard banner switched from
  "under review" to "Review complete — research only, do NOT deploy" with the verdict
  and reasons; a lead caveat card added surfacing the full review conclusion
  (real-but-undeployable, hindsight universe, 2021 concentration, gate validated).
  Lean CI added (`.github/workflows/tests.yml` — pytest on scripts/tests changes;
  daily-check untouched). Full design.md token migration DEFERRED (documented in
  `CLAUDE.md`) — disproportionate for a do-not-deploy result. Docs regenerated via
  pipeline.py.

- **2026-07-04 — Review follow-ups (four-lens quant-PM / dev / CIO / CPM critique).
  Verdict TEMPERED → MARGINAL.** Full record: `results/phase_d_followups.md`;
  harness `scripts/research/phase_d_followups.py`. (1) The edge is 2020–21-levered:
  2021 = 62% of log-growth; ex-2020-21 Sharpe 0.62 ≈ BTC 0.61; recent 2023–26 Sharpe
  0.73. (2) Honest year-block bootstrap CI = [0.37, 2.14] (the dashboard's [0.82, 1.92]
  is overconfident); P(Sharpe > BTC 0.61) = only 88%. (3) NOT a diversifier — beta 1.04
  to SPY, correlation RISES in stress (0.21 → 0.34), −0.7% in equities' worst-quartile
  months; only a modest diversifier vs the owner's gated blend (corr 0.26) because both
  de-risk together, not orthogonality. Downgrade: from "deploy small crypto sleeve" to
  "MARGINAL — a tiny return-seeking satellite at most, not a hedge; competes with adding
  crypto beta to the existing thematic sleeve." Governance: recommend re-anchoring the
  −30% ceiling to a principled −50% crypto hard-stop rather than removing it (v3.1's
  −44.8% passes either; owner to confirm). Flagged but NOT yet fixed: research harnesses
  (incl. the DSR code) are untested; no harness-vs-production parity assertion; docx
  build scripts uncommitted.

- **2026-07-05 — CORRECTION to the follow-up above.** The "(1) ex-2020-21 Sharpe 0.62 ≈
  BTC 0.61" claim was UNFAIR — it stripped the strategy's best years but compared to
  full-sample BTC. Like-for-like (same years stripped from each), the strategy beats BTC
  AND 60/40 in EVERY window: full 1.35 vs 0.61, ex-2021 0.77 vs 0.55, ex-2020-21 0.62 vs
  **0.22**, ex-2018/20/21 0.80 vs 0.59 — the margin WIDEST with the bull years removed
  (the breadth gate goes to cash in the 2018/2022 bears while BTC eats −73%/−65%). So the
  edge over passive crypto is genuine active management, not a bull-market artefact.
  Verdict revised MARGINAL → **a real but modest active-crypto edge; a small
  return-seeking satellite, not a hedge or a core** — the binding cautions are the
  uncertainty (year-block CI [0.37, 2.14], 88% > BTC) and that it does NOT diversify
  equities (beta ≈ 1), NOT that it is "secretly BTC." The engineering items flagged above
  (DSR / harness tests, parity, docx build scripts) were subsequently FIXED (commit
  a70420a: 24 tests incl. an engine-parity assertion; docx specs → `reviews/build/`).
  Ceiling re-anchored to −50% (PR-1 Amendment 2).

## REVIEW CLOSE — 2026-07-04

**Overall verdict (as amended 2026-07-04): v3.1's edge is statistically real AND
deployable as a small crypto diversifier sleeve — it beats passive crypto (BTC-HODL /
60-40) decisively on risk-adjusted return.** **[REVISED by the four-lens follow-ups
(2026-07-04), CORRECTED 2026-07-05 → a real but modest active-crypto edge; small
satellite, not a hedge:** on a like-for-like basis (same years stripped from each) the
strategy beats BTC and 60/40 in EVERY window (full 1.35 vs 0.61; ex-2020-21 0.62 vs
0.22) — the edge is genuine active management, not a bull artefact, and the earlier
"≈ BTC" line was an unfair comparison (handicapped strategy vs full-sample BTC). What
caps it at *small* is the uncertainty (year-block CI [0.37, 2.14], 88% > BTC) and that
it does NOT diversify equities (beta ≈ 1, correlation rises in stress). See
`results/phase_d_followups.md`.** The −30% MaxDD ceiling was removed post-hoc
(an equity frame mis-applied to crypto; see the PR-1 amendment). The pre-registered C.3
modification path still failed (concentration hurts; the majors engine is weaker), so
v3.1 itself — not a modification — is the deployable candidate, at small size and with
the standing caveats. **For the record: under the ORIGINAL frozen −30% rule the verdict
was MODIFY / do-not-deploy.**

- Phase B: DSR ≥0.999 (not a multiple-testing fluke), full-config WF loses only +0.08
  OOS (params not overfit), cost-robust to 50bps, weak-patch = regime-not-decay
  (on notice). MaxDD −44.8% fails the −30% ceiling; C.2 overlay best −33.3%.
- Phase 2: universe is hindsight-selected (40/40 era-majors absent) → optimistic
  survivorship bias; headline = upper bound. Splice clean, deaths honest.
- Phase 4: C.3a concentration hurts (dispersion loss); C.3b majors TS-momentum
  lower-Sharpe and still −50%+ (COVID-style 1-day gaps). No deployable candidate.
- Phase 5: breadth gate validated (load-bearing; beats BTC-200d), tiering redundant.
- **Structural finding:** nothing in the study clears −30% — a long-only spot crypto
  book cannot, given single-day −50% gaps. Deployability needs the parked C.5
  (perp/short hedge) or a small-sleeve sizing that makes a −45% book tolerable at the
  portfolio level.
- **Recommendation (amended 2026-07-04):** deploy v3.1 as a SMALL crypto diversifier
  sleeve (it beats passive crypto risk-adjusted — Sharpe 1.35 vs BTC 0.61 / 60-40 0.66),
  sized so the −45%+ book drawdown is tolerable at the portfolio level, and eyes-open to
  the survivorship-optimistic headline, the 2021 concentration, and the on-notice 2025-26
  weak patch. Optionally add the C.2 vol-target overlay to trim the tail (−44.8% → ~−33%
  at a Sharpe cost). C.5 (perp/short hedge) remains the path to a materially lower
  drawdown if a larger allocation is wanted. 99 trials logged in
  `results/trial_registry.jsonl`. (Original recommendation under the −30% rule:
  do-not-deploy — retained in the PR-1 amendment record.)
