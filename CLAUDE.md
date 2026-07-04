# CLAUDE.md — Crypto-Breadth

Layers on top of `C:\dev\CLAUDE.md` (vault rules). Divergences and hard rules specific
to this project. Read `README.md`, `RESEARCH_MEMO.md`, and `DATA_INTEGRITY_POLICY.md`
here first.

## Posture

Research-grade, no capital yet. Currently under a bte-quality robustness review
(Phase B) that may keep / modify / retire v3.1. The public dashboard carries an "under
review" banner for the duration. Scope + pre-registration:
`C:\dev\KICKOFF_crypto-breadth-uplift.md` and `RESEARCH_MEMO.md` (PR-1…PR-4 frozen
2026-07-04).

## Data layer

- **Substrate = `data/prices.parquet` (Binance USDT spot) ONLY.** No new data vendor
  (Norgate verified irrelevant to crypto: BTC / ETH / XRP USD + 3 S&P indices only).
  The project is un-gated on the 24 July Platinum decision.
- CryptoCompare is **operational-only** (daily alert tail). Never the review substrate.
  See `DATA_INTEGRITY_POLICY.md` §4 for the splice guard.
- The universe is point-in-time; survivorship is under audit (Phase 2). Do not trust
  historical numbers until that audit is recorded.

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
  majors engine). Never cross-contaminate the trial counts.

## Tests

- pytest suite in `tests/` (synthetic panels, no network / parquet) — run
  `python -m pytest tests/`.
- `scripts/test_backtest.py` is the end-to-end regression smoke test wired into
  `daily-check.yml` — do NOT break that wiring; it needs the real parquet.

## Dashboard

- **NEVER open `docs/index.html` (862KB, >500KB rule).** Edit `template.html` (119KB)
  via grep anchors; regenerate with `python scripts/pipeline.py`. Never hand-edit the
  built output.
- Style per `C:\dev\design.md`. Dashboard conformance + banner removal are Phase 6
  (cosmetic-A), and only for numbers that survive Phase B.
- Local preview: `python scripts/pipeline.py` then `npx serve docs`; or `npx serve .`
  and open `template.html` (fetch fallback).

## Model

- Interview AND build on Opus 4.8 (per kickoff). The remaining Fable allowance is
  reserved for the breadth-thrust-etf implementation audit.
