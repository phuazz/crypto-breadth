# Review follow-ups — honest CI + diversification (2026-07-04)

Prompted by a four-lens (quant PM / developer / CIO / CPM) critique of the Phase-B
review. Reproducible: `scripts/research/phase_d_followups.py` →
`results/phase_d_followups.json`. These findings **temper the KEEP verdict**.

## 1. The edge leans on two bull years

| Window | CAGR | Sharpe | MaxDD |
|---|---|---|---|
| Full sample | 75.5% | 1.35 | −44.8% |
| Ex-2021 | 23.6% | 0.77 | −44.8% |
| Ex-2020 **and** 2021 | 13.7% | **0.62** | −49.4% |
| Recent regime 2023–26 | 24.3% | 0.73 | −44.8% |

2021 is **62% of total log-growth**; leave-one-year-out Sharpe moves only for 2021
(0.77) — every other year leaves it at 1.26–1.54. **Net of the two bull years the
Sharpe (0.62) equals BTC's (0.61).** The full-sample edge over passive crypto is a
2020–21 phenomenon.

## 2. Honest confidence interval — year-block bootstrap

The dashboard CI **[0.82, 1.92]** uses short blocks and overstates confidence when one
year drives the result. Resampling **whole calendar years** (respecting the 2021
dominance and within-year serial structure, n=5000):

- Sharpe **p05 / p50 / p95 = 0.37 / 1.31 / 2.14** — the lower tail sits *below* BTC.
- P(Sharpe > 0) = 99%; **P(Sharpe > BTC 0.61) = 88%**; P(Sharpe > 1) = 70%.

So the edge is probably real (median 1.31) but with a **~1-in-8 chance it is no better
than simply holding BTC**, and a genuinely weak lower tail. The displayed CI should be
read as the year-block [0.37, 2.14], not [0.82, 1.92].

## 3. Diversification — it amplifies equity risk, it does not hedge it

Monthly returns, crypto-breadth vs each series:

| vs | corr (full) | corr 2022 | corr 2025+ | beta | cb mean in other's worst-quartile months |
|---|---|---|---|---|---|
| **SPY (equities)** | +0.21 | +0.27 | +0.34 | **1.04** | **−0.7%** (vs +6.6% overall) |
| Deployed breadth-thrust blend | +0.26 | +0.01 | +0.26 | 1.64 | +0.2% (vs +7.7% overall) |

- **Against equities it fails as a diversifier**: beta ≈ 1, correlation *rises* in
  stress (0.21 → 0.34), and it is negative in equities' worst months. It is a risk-on
  asset that falls *with* equities when it matters — the opposite of a hedge.
- **Against the owner's gated blend it is only a modest diversifier** (corr 0.26,
  ~flat in the blend's worst months) — and only because *both* books de-risk in the
  same crises (breadth gates going to cash together), not because crypto is orthogonal.
  Structurally it is the same "risk-on when breadth is healthy, cash otherwise" bet on
  a more volatile asset; the benefit is regime-timing coincidence and evaporates if
  either gate fails to de-risk in a fast crash. Note also the **overlap**: the blend's
  thematic sleeve already carries spot-crypto beta.

## Revised verdict

Downgrade from "KEEP — deploy as a small crypto sleeve (beats passive crypto)" to:

**MARGINAL. The edge is probably real but uncertain (year-block CI [0.37, 2.14]; 88% >
BTC) and concentrated in two bull years; net of them it is BTC-like. It is not a
diversifier — it amplifies equity risk (beta ≈ 1, correlation rises in stress). If held
at all, a TINY return-seeking satellite, not a hedge — and it competes directly with
simply adding crypto beta to the existing thematic sleeve.** The recent-regime Sharpe
(0.73) and a −45% drawdown are what a deployer would actually experience.

## Governance — the −30% ceiling

The Phase-B amendment *removed* the pre-registered −30% MaxDD ceiling after it produced
"do-not-deploy" — outcome-motivated, even if logged. **Recommended governance:
re-anchor rather than remove** — a principled crypto hard-stop of **−50%** (shallower
than passive crypto's −81%, chosen on principle not to clear this strategy). v3.1's
−44.8% passes either framing, so the deploy decision is unchanged, but the record is
cleaner. Owner to confirm.
