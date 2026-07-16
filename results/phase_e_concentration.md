# Phase E — concentration floor (PR-5)

Pre-registered 2026-07-15 (RESEARCH_MEMO PR-5), run 2026-07-15. Harness:
`scripts/research/phase_e_concentration.py` → `results/phase_e_concentration.json`.
Guards asserted before any metric was read: `tests/test_phase_e_floor.py` (6 tests).
Frozen v3.1 engine NOT modified; every arm reshapes the rank weights or the gate
before `build_target_weights`, inheriting the close-T → trade-T+1 lag unchanged.

## Verdict

**The floor is cheap insurance, NOT an edge improvement. Adopt on principle if at
all — do NOT adopt on the measured drawdown gain, which is not a reliable forward
estimate.**

All five arms mechanically clear the pre-registered bar. That clean sweep is the
least interesting thing in this record, and taking it at face value would be a
mistake. The two findings that matter are that the arms are statistically
indistinguishable from one another, and that the measured gain rests on a
degenerate era plus a single modern event.

## All five arms (reported in full, per guard 1)

Baseline reproduces the recorded review figures exactly (Sharpe 1.35, MaxDD
−44.8%, CAGR 75.2%), which is the evidence that the harness is faithful.

| arm | Sharpe | ΔSharpe | CAGR | MaxDD | ΔMaxDD | 100% 1-name | max name wt | cond. fwd-14d worst | DSR (N=84) | clears bar |
|---|---|---|---|---|---|---|---|---|---|---|
| frozen v3.1 (control) | 1.349 | — | 75.2% | −44.8% | — | **5** | 100.0% | −14.2% | 0.9986 | — |
| E.1 floor k=2 | 1.397 | +0.048 | 78.1% | −37.5% | +7.25pp | 0 | 50.0% | −3.6% | 0.9991 | yes |
| E.1 floor k=3 | 1.354 | +0.005 | 73.9% | −37.5% | +7.25pp | 0 | 33.3% | −5.8% | 0.9986 | yes |
| E.2 cap c=0.34 | 1.360 | +0.011 | 75.0% | −39.5% | +5.29pp | 0 | 34.0% | −7.0% | 0.9987 | yes |
| E.2 cap c=0.50 | 1.352 | +0.003 | 74.6% | −41.3% | +3.48pp | 0 | 50.0% | −10.3% | 0.9986 | yes |
| E.3 pro-rata | 1.374 | +0.025 | 75.4% | −38.4% | +6.35pp | 0 | 25.0% | −4.5% | 0.9989 | yes |

## Why the sweep must not be read as five wins

**1. The arms are statistically indistinguishable.** Full-sample Sharpe standard
error over 8.5 years is ≈ **0.342**. The entire spread across arms is +0.003 to
+0.048 — about **one seventh of one standard error**. Ranking the arms by Sharpe,
or picking k=2 because it posted the highest, would be noise-mining of exactly the
kind the DSR exists to punish. Treat all five as tied on edge.

**2. The measured MaxDD gain is one drawdown window.** Every arm's full-sample
MaxDD IS its loss inside the single 2024-12-02 → 2026-06-06 window. Year by year,
E.1 k=2 is **identical to the baseline in 2020, 2021, 2022, 2023, 2024 and 2025**.
It differs only in 2018 (−26.0% → 0.0%) and 2026 (−22.7% → −13.0%). The headline
improvement is therefore approximately two episodes, not a persistent effect.

**3. The historical realisations are dominated by a degenerate universe.** E.1 k=2
binds on 17 risk-on Mondays in 8.5 years — **13 of them in 2018–2019**, when the
median investable universe was **3 and 5 coins** respectively. With three coins
investable, "only one is eligible" is unremarkable, and PR-1 already names the 2018
three-coin whipsaw as a known structural artifact. Only **4** binding Mondays fall
in 2020–2026. Of the five 100%-single-name events, **four are 2018–2019** and only
**one is modern**: NEAR, 2026-03-16.

| year | risk-on Mondays | n_elig = 1 | n_elig ≤ 2 | 100% 1-name | median investable |
|---|---|---|---|---|---|
| 2018 | 8 | 8 | 8 | 2 | **3** |
| 2019 | 28 | 5 | 8 | 2 | **5** |
| 2020 | 37 | 1 | 5 | 0 | 8 |
| 2021 | 35 | 0 | 0 | 0 | 24 |
| 2022 | 17 | 1 | 1 | 0 | 19 |
| 2023 | 35 | 0 | 2 | 0 | 14 |
| 2024 | 36 | 0 | 2 | 0 | 17 |
| 2025 | 26 | 1 | 1 | 0 | 16 |
| 2026 | 14 | 1 | 2 | **1** | 9 |

Full `n_eligible` distribution over all 236 risk-on rebalances (guard 2 — never
just the five events): 1 name 17 (7.2%), 2 names 12 (5.1%), 3 names 10 (4.2%),
4 names 25 (10.6%), then a long tail to 25. The 29 thin events (`n_eligible ≤ 2`)
are spread across 2018–2026 and are NOT clustered in one episode — but as the
table shows, they concentrate heavily in the early thin-universe years.

## What is actually established

- The concentration property is **real and verified**: `rank_top_n` deploys the
  full tier across however many names qualify, and it has put 100% of the book
  into one coin five times.
- Removing it costs **nothing measurable**: no arm loses Sharpe (all within noise),
  none worsens MaxDD, all clear the −50% hard-stop, all keep DSR ≥ 0.9986.
- But the exposure is **rare in the modern universe** — once since 2020. The
  measured −44.8% → −37.5% is not an expected forward gain; it is what happened
  in two specific episodes, one of them a degenerate 3-coin era.

So the case for a floor is **not** "it improves drawdown by 7pp". It is: the engine
carries a structural tail that can put the whole book in one altcoin, that tail is
catastrophic at size, and closing it is free. That is an insurance argument, and it
should be made on principle rather than on this backtest's point estimate.

## Recommendation

Adopt **E.2 cap c=0.34** if adopting. Reasoning is governance, not metrics (the
metrics cannot separate the arms): it states a continuous, always-on rule — *no
single name above a third of the book* — that binds only when it must, needs no
arbitrary count threshold, and does not depend on the eligibility count being a
meaningful signal. E.1 k=2 is the surgical alternative (it acts only on the truly
degenerate one-name case and is otherwise inert), but it still permits a 50%
single-name position at the 100% tier, which is a weaker guarantee.

**Adoption is conditional and NOT complete.** Any variant is a MODIFY of v3.1 under
PR-1, so the chosen arm must clear PR-1 KEEP (1)–(3) in full. PR-5 ran KEEP-1 (DSR,
above) but **did NOT run KEEP-2**, the full-config expand-window walk-forward, on
any variant. That must be run on the chosen arm before it replaces v3.1. Until
then v3.1 remains the recorded baseline.

## Honest notes against this record

1. **My E.3 prediction was wrong.** PR-5 registered E.3 as "the most obvious fix
   and the most suspect… registered as the honest comparator, not the expected
   winner", on the grounds that it double-counts near-collinear filters. It did not
   lose: +0.025 Sharpe, +6.35pp MaxDD, and it delivers the tightest diversification
   guarantee of any arm (max name weight 25%). The stated objection did not
   materialise as a cost. Recorded rather than quietly dropped.
2. **PR-5's frozen "DSR over N=104" was wrong on the memo's own rule.** 104 counts
   ALL logged trials (99) + 5, but the v3.1 pool is B+C2+C3a+C4 = 79 and C.3b's 20
   belong to a SEPARATE pool that must not cross-contaminate. The correct v3.1
   figure is **N=84**, reported above. The pre-registered N=104 gives DSR 0.9984 —
   materially identical, since DSR is ≥0.998 across N=10→200 — so nothing turns on
   it, but the error is logged rather than silently corrected to the preferred
   number.
3. **PR-5 did not pre-specify a tie-break.** The bar was written expecting at most
   one arm to clear; five did. The recommendation above is therefore a post-hoc
   judgement on governance grounds, explicitly NOT a selection on the metrics —
   which is why the metrics are declared tied rather than used to rank.
4. **Guard-2 risk partially realised.** The pre-registration warned that "a variant
   can look good merely by dodging one bad draw". That is close to what happened:
   the modern-era gain is essentially the NEAR event. The guard did its job — the
   full distribution and the per-year attribution are what exposed it — but the
   headline numbers in the table would mislead if read alone.

## Trials logged

5 appended to `results/trial_registry.jsonl` under arm `E`. v3.1 pool 79 → **84**.
Total logged 99 → **104**. C.3b's pool (20) untouched.
