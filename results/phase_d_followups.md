# Review follow-ups — like-for-like, honest CI, diversification

Started 2026-07-04 (four-lens quant / developer / CIO / CPM critique); **§1 corrected
2026-07-05** after the "net of 2020–21 ≈ BTC" framing was challenged and found unfair.
Reproducible: `scripts/research/phase_d_followups.py` → `results/phase_d_followups.json`.

## 1. The edge over passive crypto is real — like-for-like (correction)

An earlier draft said "net of 2020–21 the Sharpe (0.62) ≈ BTC (0.61)." That was a
**rigged comparison** — it stripped the strategy's best years but left BTC at full
sample. Stripping the **same years from each** is the honest test:

| Window | Strategy | BTC | 60/40 | Winner |
|---|---|---|---|---|
| Full | 1.35 | 0.61 | 0.63 | strategy |
| Ex-2021 | 0.77 | 0.55 | 0.47 | strategy |
| Ex-2020–21 | 0.62 | **0.22** | **0.10** | strategy |
| Ex-2018/20/21 | 0.80 | 0.59 | 0.41 | strategy |

The strategy **beats Bitcoin and 60/40 in every window**, and the *relative* margin is
**widest with the bull years removed** (ex-2020–21: 0.62 vs 0.22) — because its breadth
gate goes to cash in the 2018/2022 bears while Bitcoin takes the full −73% / −65%. The
edge is genuine active management (the gate does real work, most of it in bad years),
**not** a bull-market artefact or a closet index. 2021 being 62% of log-growth simply
reflects that 2021 was a huge crypto year for *everyone* — Bitcoin and 60/40 made most
of their money then too; stripping it hurts them at least as much.

## 2. The edge is real but uncertain — year-block bootstrap

The dashboard's earlier [0.82, 1.92] used short blocks and overstated confidence when
one year drives the result. Resampling **whole calendar years** (n=5000):

- Sharpe **p05 / p50 / p95 = 0.37 / 1.31 / 2.14**.
- P(Sharpe > 0) = 99%; **P(Sharpe > BTC 0.61) = 88%**; P(Sharpe > 1) = 70%.

Probably real (median 1.31), but with a **~1-in-8 chance it is no better than Bitcoin**
over a future run. The dashboard hero now uses this year-block CI.

## 3. It is not a diversifier — it amplifies equity risk

Monthly returns, crypto-breadth vs each series:

| vs | corr (full) | corr 2022 | corr 2025+ | beta | cb mean in other's worst-quartile months |
|---|---|---|---|---|---|
| **SPY (equities)** | +0.21 | +0.27 | +0.34 | **1.04** | **−0.7%** (vs +6.6% overall) |
| Deployed breadth-thrust blend | +0.26 | +0.01 | +0.26 | 1.64 | +0.2% (vs +7.7% overall) |

Against equities it **fails as a diversifier**: beta ≈ 1, correlation *rises* in stress
(0.21 → 0.34), and it is negative in equities' worst months — it falls *with* equities
when it matters. Against the owner's already-gated blend it is only a modest diversifier
(corr 0.26), and only because *both* books de-risk in the same crises (their breadth
gates going to cash together), not because crypto is orthogonal. Note the **overlap**:
the blend's thematic sleeve already carries spot-crypto beta.

## Verdict

**A real but modest active-crypto edge — a small return-seeking satellite, not a hedge
or a core.** The edge over passive crypto is genuine and holds like-for-like in every
window (the gate does real work, most of it in bears). What caps it at *small* is not
that it is "secretly Bitcoin" — it is not — but that (a) the edge is uncertain (honest
CI [0.37, 2.14]; 88% > BTC) and, decisively, (b) it does **not** diversify equities
(beta ≈ 1, correlation rising in stress), and (c) the absolute headline is
survivorship-optimistic. Suitable as a tiny satellite for someone who actively wants
crypto exposure; it competes with simply holding a little crypto beta.

## Governance — the −30% ceiling

The Phase-B amendment first *removed* the pre-registered −30% ceiling (outcome-motivated,
though logged). Superseded by a **principled re-anchor to a −50% crypto hard-stop**
(shallower than passive crypto's −81%, chosen on principle, not to clear this strategy).
v3.1's −44.8% passes, so the deploy decision is unchanged, the record cleaner.
