# CLAUDE.md — Crypto-Breadth

Layers on top of `C:\dev\CLAUDE.md` (vault rules). Divergences and hard rules specific
to this project. Read `README.md`, `RESEARCH_MEMO.md`, and `DATA_INTEGRITY_POLICY.md`
here first.

## Posture

Research-grade, **no capital deployed**. The bte-quality review is **COMPLETE**
(Phases 1–6, 2026-07-04, plus the four-lens follow-up corrected 2026-07-05) — it is no
longer "under review". Scope + pre-registration:
`C:\dev\KICKOFF_crypto-breadth-uplift.md` and `RESEARCH_MEMO.md` (PR-1…PR-4 frozen
2026-07-04). Ledger row: `C:\dev\STUDIES_LEDGER.md` (2026-07-04, Crypto-Breadth).

**Verdict = KEEP — a real but modest active-crypto edge; a tiny return-seeking
satellite, NOT a hedge and NOT a core.** Edge is statistically real (DSR ≥0.999,
walk-forward loss +0.08, cost-robust) and beats passive crypto risk-adjusted (Sharpe
1.35 vs BTC 0.61; MaxDD −44.8% vs BTC −81%). Three standing caveats travel with every
number: the headline is survivorship-optimistic (see Data layer), the edge is
uncertain (honest year-block Sharpe CI [0.37, 2.14]; P(Sharpe > BTC) = 88% only), and
it is not a diversifier (beta ≈ 1.0 to SPY, correlation RISES in stress).

**ENGINE VERSIONS — the review measured v3.1; v3.2 is what trades.** Every figure
above and in `results/` is a **v3.1** figure. **v3.2** (adopted 2026-07-16, PR-5 arm
E.2) = v3.1 + a **34% single-name cap**; it measures Sharpe 1.359 / MaxDD −39.5% and
clears KEEP (1)–(3) independently (DSR 0.9987, WF loss +0.085, weak patch
bit-identical). It is **insurance, not an edge gain** — the drawdown difference rests
on ~2 episodes and the Sharpe gap is ~1/7 of one SE. Do not quote v3.2's −39.5% as if
the review had produced it, and do not "update" the v3.1 records to it.
`build_target_weights(single_name_cap=...)` defaults to **None = v3.1 bit-for-bit**,
which is why the `phase_*` record harnesses still reproduce (pinned by
`tests/test_v31_reproducibility.py` — do NOT make them pass the cap). Production
paths pass `p.single_name_cap`. Reproduce v3.1 with `Params(single_name_cap=None)`.

Deployment gate history — do not misread this: the original frozen −30% MaxDD ceiling
made v3.1 **do-not-deploy**, but that ceiling was **removed post-hoc at owner
instruction** (logged transparently as an equity frame mis-applied to crypto). The
governance recommendation on record is a principled **−50% crypto hard-stop**, which
−44.8% passes. "Do-not-deploy" is retained only under the ORIGINAL −30% rule, for the
record. The dashboard banner accordingly reads "Review complete", not "under review".

## Data layer

- **Substrate = `data/prices.parquet` (Binance USDT spot) ONLY.** No new data vendor
  (Norgate verified irrelevant to crypto: BTC / ETH / XRP USD + 3 S&P indices only).
  The project is un-gated on the 24 July Platinum decision.
- **Operational daily tail = Binance's market-data mirror `data-api.binance.vision`**
  (`scripts/fetch_daily_update.py`). Same exchange as the substrate, no API key, not
  US-geoblocked (unlike `api.binance.com`, which 451s from GitHub runners). It appends
  only CLOSED daily candles.
- **CryptoCompare is RETIRED (2026-07-14)** — its free tier rate-limited and the key
  exhausted, freezing the parquet for ten days behind a green CI. `DATA_INTEGRITY_POLICY.md`
  §4's splice guard is now **legacy**: it applies only to rows appended before
  2026-07-14. The tail is Binance-native from that date, so there is no live
  cross-vendor splice.
- **The universe is NOT point-in-time.** The Phase-2 audit is RECORDED
  (`results/survivorship_audit.md`): it is a fixed, hindsight-selected 25-coin list —
  40 of 40 era-majors that carried real 2019–21 liquidity are absent, and no name in
  the set ever dies. The bias is material, one-directional and optimistic, so **every
  headline figure is an UPPER BOUND** and must carry that caveat. LUNA/FTT deaths are
  honestly captured; the historical splice tested clean.
- EOS and MATIC were rebranded on Binance (→ Vaulta / POL), so their legacy `*USDT`
  pairs return no data and are frozen (`DELISTED_ON_BINANCE` in the fetch script).
  Both sit outside the investable set, so the live signal is unaffected. Whether to
  remap them, and the fact that LUNA now draws Binance's LUNA 2.0 rather than
  CryptoCompare's LUNC, are open Phase-2 items.

## Engine discipline

- **v3.1 (`scripts/backtest.py`) is the FROZEN review baseline.** No silent
  re-parameterisation. Any parameter change is a named, pre-registered trial in
  `results/trial_registry.jsonl`.
- No look-ahead: signals observed at close T, traded at close T+1 (the `.shift(1)` in
  `run_backtest`). Every new signal respects this; the tests assert it.
- Realistic costs always (10 bps/side maker baseline; Phase-B stress adds taker +
  spread-by-tier + 2×).
- **UTC calendar.** All boundaries / timestamps in UTC; no weekday assumptions from
  equity code.
- Two SEPARATE DSR deflation pools: v3.1 (B / C.2 / C.3a / C.4) and C.3b (the new
  majors engine). Never cross-contaminate the trial counts. 99 trials logged.
- **Auto-concentration — FOUND 2026-07-15, RESOLVED in v3.2 (PR-5 arm E.2).**
  `rank_top_n` equal-weights `1/min(n, len(valid))`, so the engine deploys the FULL
  tier across however many names are trend-eligible: it CONCENTRATES when eligibility
  is thin rather than under-deploying. On v3.1, of 236 risk-on rebalance Mondays 39
  held fewer than four names, 17 held exactly one, and **5 put 100% of the book into a
  single coin** — most recently NEAR on 2026-03-16, where breadth was 100% (⇒ the full
  100% tier) but only NEAR had a rising MA, so maximum exposure met minimum
  diversification. It fell 20%. **v3.2's 34% cap closes this** (binds 14/236). The
  −44.8% MaxDD in the v3.1 records was NOT measured on a single-name book — that is
  why the cap matters for SIZING, and it is the reason not to quote v3.1's drawdown
  for a v3.2 book. PR-5 registered 5 trials (v3.1 pool 79→84); the other four arms
  (E.1 k∈{2,3}, E.3 pro-rata) were tested and NOT adopted — all five cleared the bar,
  and the arms are statistically indistinguishable, so E.2 was chosen on governance
  grounds, not metrics. Do not re-litigate by re-running arms ad hoc.
- `rebalance_weekday = 0` (Monday) is **not** in the OAT grid — the "6 of 7 parameters
  robust" claim does not cover it. The gate is read on Monday's close only, so a
  breadth crossing on any other day waits for the next Monday. A sweep is a Phase-B
  candidate.

## Tests

- pytest suite in `tests/` (synthetic panels, no network / parquet) — run
  `python -m pytest tests/`.
- `scripts/test_backtest.py` is the end-to-end regression smoke test wired into
  `daily-check.yml` — do NOT break that wiring; it needs the real parquet.

## Dashboard

- **NEVER open `docs/index.html` (~893KB, >500KB rule).** Edit `template.html` (~129KB)
  via grep anchors; regenerate with `python scripts/pipeline.py`. Never hand-edit the
  built output. Re-check both sizes with `wc -c` rather than trusting these figures.
- **Book vs gate.** The dashboard and the email digest must never conflate what is
  HELD with what the breadth gate TARGETS. They legitimately diverge: the gate is read
  at Monday's close and executes the next bar, so a mid-week crossing is not yet
  actionable. Anything describing the book keys off the actual holdings; only
  gate/target lines use `target_exposure`. Display sizing must mirror
  `backtest.rank_top_n` — see `pipeline.intended_per_coin_weight` and
  `tests/test_intended_allocation.py`, which pin the parity.
- Style per `C:\dev\design.md`. NOTE (2026-07-04): this dashboard predates design.md
  and uses its own token system (`--ink`, `--accent`, `--warn`), not design.md's
  (`--t1`, `--a`, `--serif`). A full token migration was **deliberately deferred** in
  Phase 6, justified at the time by the do-not-deploy conclusion. **That rationale is
  now superseded** (the verdict is KEEP-as-small-satellite), so the deferral is an
  OPEN decision rather than a settled one — but it is still not automatically worth
  doing. Ask before starting it.
- Local preview: `python scripts/pipeline.py` then `npx serve docs`; or `npx serve .`
  and open `template.html` (fetch fallback).

## Model

- Interview AND build on Opus 4.8 (per kickoff). The remaining Fable allowance is
  reserved for the breadth-thrust-etf implementation audit.
