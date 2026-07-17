# Crypto Breadth & Momentum

Cross-sectional momentum strategy on crypto majors, gated by breadth and
per-coin trend filters. Binance USDT spot, 25-coin rolling-liquidity
universe, daily exit overlay. Research-grade — not deployed, no live
track record.

Repo slug remains `crypto-breadth` for URL continuity.

**Live dashboard:** [phuazz.github.io/crypto-breadth](https://phuazz.github.io/crypto-breadth/)
— interactive Plotly equity curve, drawdown, regime breakdown, bootstrap
Sharpe distribution and parameter sensitivity. Auto-generated from
`scripts/pipeline.py`.

Current version: **v3.2** (live) — v3.1 plus a 34 % single-name cap, adopted
2026-07-16. **The 2026-07-04 review measured v3.1, so every figure in `results/`
is a v3.1 figure**, including the −44.8 % max drawdown that the −50 % deployment
ceiling and the satellite sizing rest on; see [Engine versions](#engine-versions--v31-the-review-vs-v32-live)
below before quoting any number. The strategy, the file layout, and the honest
caveats are all below. For the full session history that produced this, see
the commit log. v3.1 adopted top-4 + (30, 90, 180) momentum lookbacks after
an expand-window walk-forward picked that config in all seven annual
re-fits — see `scripts/walk_forward_refit.py`.

---

## What the strategy does (v3)

- **Universe:** 25 USDT pairs on Binance, with a **rolling liquidity gate** —
  a coin is investable on date T only if it has ≥ 90 days of history and its
  trailing 30-day average daily $ volume is ≥ $25 M. **Caveat (2026-07-04
  audit):** this gate removes survivorship bias *within* the 25 names, but the
  25 are themselves a hindsight-selected set. A truly point-in-time Binance USDT
  universe would include ~40+ coins that were liquid in 2019–2021 and later died
  or faded (all 40 era-majors checked are absent; the pool is < 40% of the true
  era-liquid set). The headline figures therefore carry an **optimistic
  survivorship-selection bias**, concentrated in the alt-heavy years, and should
  be read as an upper bound. See `results/survivorship_audit.md`.
- **Breadth gate:** % of investable universe trading above its 50 d MA, mapped
  to tiered gross exposure (0 / 30 / 60 / 100 %). The tier graduation
  contributes almost nothing — a binary (0, 0, 0, 1) gate gives essentially
  the same Sharpe — but the gate itself is structurally important
  (gate ablation: +0.25 Sharpe, +30 pp MaxDD improvement).
- **Sizing:** composite momentum score (risk-adjusted returns over 30, 90 and
  180 d), top-4 equal-weight when on. Adopted in v3.1 after an expand-window
  walk-forward picked this config in all seven annual re-fits (2020 → 2026).
- **Single-name cap (v3.2, 2026-07-16):** no name is **targeted** above **34 %**
  of the book at a rebalance; the residual falls to cash. Fewer than four names
  often qualify (39 of 236 risk-on rebalances), and the engine spreads the *full*
  tier across whichever do — so v3.1 could put 100 % of the book in one coin, and
  did on five occasions (most recently NEAR, 2026-03-16, at the full 100 % tier:
  −20 %). The cap binds on 14 of 236 rebalances.
  **It is a rebalance-day target, not a standing portfolio constraint.** Weights
  drift with prices between Mondays and are not re-capped intra-week, so a
  *realised* weight can run above 34 %: max **41.4 %** over the sample (BNB,
  2021-02-19; 54 of 3 119 days above 34 %, one above 40 %, none above 50 %).
  Against v3.1's 100 % that is the material improvement, and continuous
  re-capping is deliberately **not** done — it would add turnover and break the
  weekly cadence for no benefit. It is **insurance, not an edge gain** — see the
  versioning note below.
- **Trend entry filter:** a coin can only enter the top-N rank if close > own
  50 d MA AND the MA is rising. Strict — designed to avoid head-fakes.
- **Trend exit filter:** asymmetric — close < own 50 d MA triggers a forced
  sell at the **next** close (1-bar lag). Looser than the entry rule, to
  avoid whipsawing on normal pullbacks.
- **Cadence:** weekly Monday rebalance + daily exit overrides. Signal observed
  at close T, traded at close T + 1.
- **Fees:** 10 bps per side (Binance VIP-0 spot).

---

## Engine versions — v3.1 (the review) vs v3.2 (live)

The 2026-07-04 review measured **v3.1**. **Every review figure in this README, in
`results/` and on the dashboard is a v3.1 figure**, including the −44.8 % max
drawdown that the −50 % deployment ceiling and the satellite sizing rest on. Those records
stand as written and remain reproducible: `build_target_weights` defaults to no
cap, so the `scripts/research/phase_*.py` harnesses reproduce them bit-for-bit
(pinned by `tests/test_v31_reproducibility.py`).

**v3.2 (2026-07-16) is what actually trades**: identical to v3.1 plus the 34 %
single-name cap. It clears PR-1 KEEP (1)–(3) in its own right — DSR 0.9987,
walk-forward loss +0.085 against a 0.30 tolerance, and a weak-patch book that is
*bit-identical* to v3.1 because the cap never binds in 2024–25.

| | full Sharpe | MaxDD | walk-forward loss |
|---|---|---|---|
| v3.1 (the review record) | 1.349 | −44.8 % | +0.080 |
| **v3.2 (live)** | **1.359** | **−39.5 %** | **+0.085** |

Do **not** read the drawdown difference as an improved strategy. It rests on ~2
episodes (one of them the degenerate 2018 three-coin era), and the Sharpe gap is
roughly one seventh of one standard error. The cap costs 143 pp of cumulative
return over 8.5 years (~1.2 % of the total) and removes every 100 %-single-name
book: **cheap insurance against a rare, catastrophic tail — not an edge gain.**
Records: `results/phase_e_concentration.md`, `results/phase_e_keep2_walkforward.md`.

To reproduce v3.1 exactly, set `Params(single_name_cap=None)`.

---

## Performance (full sample 2018-01-01 → 2026-07-16)

> Re-cut 2026-07-16 from the live build. The previous table was a hand-maintained
> snapshot at sample end 2026-05-28 (before the June-2026 trough) reading
> 1.37 / −42.5 %, which by then matched neither the filed v3.1 record (−44.8 %)
> nor the live engine — a third number wearing a "v3.1" label. Both engines are
> shown below so that cannot recur. The dashboard is the authority; this table is
> a convenience copy and will drift as data advances.

| series | CAGR | Sharpe | MaxDD |
|---|---|---|---|
| **strategy — v3.2 (LIVE)** | **74.9 %** | **1.359** | **−39.5 %** |
| strategy — v3.1 (the review record) | 75.2 % | 1.349 | −44.8 % |
| BTC HODL | 20.1 % | 0.61 | −81.2 % |
| equal-weight investable | 14.7 % | 0.57 | −82.8 % |
| 60/40 BTC/ETH | 21.6 % | 0.64 | −85.5 % |

**Out-of-sample (2021-01-01 → 2026-07-16), v3.2:** CAGR 90.3 %, Sharpe 1.456,
MaxDD −39.5 %.

**Size on the v3.1 number, not the v3.2 one.** The −50 % deployment ceiling and the
satellite sizing rest on v3.1's **−44.8 %**. v3.2's −39.5 % removes a tail that
was armed but never fired during a large drawdown, so v3.2's true forward risk is
lower than v3.1's by an *unmeasurable* amount — **not** by the measured 5.3 pp.
Ratcheting the position up because the backtested drawdown improved is the single
worst inference available here.

**Year-block bootstrap 90 % CI on full-sample Sharpe: [0.37, 2.14]**
(p05 / p50 / p95 = 0.37 / **1.31** / 2.14, n = 5 000). P(Sharpe > 0) = 99 %;
**P(Sharpe > BTC's 0.61) = 88 % only** — roughly a 1-in-8 chance it is no better
than simply holding Bitcoin over a future run.

> Corrected 2026-07-16. This README previously quoted **[0.82, 1.92]**, which the
> Phase-D follow-up found **overconfident**: short blocks understate uncertainty
> when one year drives the result (2021 is 62 % of log-growth). Resampling whole
> calendar years is the honest construction. The dashboard was corrected at the
> time; this file was missed. See `results/phase_d_followups.md` §2.

**Parameter sensitivity (IS only, OAT ±50 %):** 6 of 7 parameters ROBUST or
MILDLY SENSITIVE. The 7th (`per_coin_trend_window`) is fragile in IS but
stable in OOS. No parameter's default sits at the IS peak — no evidence of
implicit IS-tuning.

---

## What this strategy is not

Four things are important to say upfront, because the headline numbers
above will mislead anyone who skips them:

1. **It is not a strict BTC-improvement.** It underperforms BTC in pure
   BTC-led bull years (2020, 2023, 2024). It outperforms in bears (2018,
   2022) and in breakout altcoin years (2019, 2021). It is a *different*
   risk profile, not a better one across all regimes.
2. **It is currently in a real weak patch.** Anyone deploying since
   2024-01-01 would have a 1.4-year Sharpe of −0.48 vs 60/40's −0.17. The
   strategy has been underperforming for ~18 months as of writing.
3. **The 2018 bear is brutal.** −43 % CAGR in 2018 because the rolling
   liquidity universe had only 3 coins investable that year (BTC, BNB, ETH)
   and the trend filter whipsawed on false bounces. This is a known wound.
4. **Out-of-sample is not pristine.** The v3.1 parameter choice (top-4,
   lookbacks 30 / 90 / 180) was selected via an expand-window walk-forward
   spanning 2020 → 2026 in `scripts/walk_forward_refit.py`. The IS-best
   config matched the production default in all seven annual re-fits, which
   is unusually clean — but the post-2021 dataset has been *looked at* as
   part of the validation. The headline OOS Sharpe and the 90 % bootstrap
   CI [0.37, 2.14] are therefore **post-selection** and assume regime
   stationarity. Truly untouched out-of-sample begins **2027-01-01**.

---

## File layout

```
crypto-breadth/
├── template.html              # source for the GitHub Pages microsite
├── scripts/
│   ├── fetch_data.py          # daily Binance OHLCV (cron candidate)
│   ├── backtest.py            # PRODUCTION strategy v3
│   ├── pipeline.py            # template.html + data -> docs/index.html
│   ├── walk_forward.py        # year-by-year + block-bootstrap Sharpe CI
│   ├── sensitivity.py         # OAT parameter robustness check
│   ├── generate_tearsheet.py  # one-page PNG summary
│   ├── requirements.txt
│   └── research/
│       ├── backtest_v0.py     # bug-fixed v0 archive (fixed-10 universe)
│       ├── backtest_v1.py     # fixed-10 + trend / vol-target side-by-side
│       ├── diagnostics.py     # gate ablation / fee sensitivity / attribution
│       └── vol_target_search.py  # IS-only vol-target grid (rejected)
├── data/
│   ├── prices.parquet         # daily OHLCV, long format
│   ├── prices_meta.json       # investability summary
│   ├── backtest_equity.parquet
│   ├── backtest_diagnostics.png
│   ├── dashboard_data.json    # sidecar consumed by docs/index.html
│   └── tearsheet.png
├── docs/
│   └── index.html             # GitHub Pages build output (served as the site)
└── README.md (this file)
```

The four scripts in `scripts/` are the production / operational set.
`scripts/research/` holds investigations that informed v3 — they are kept
for audit but are not part of the daily pipeline.

---

## Run

```
python -m venv .venv
.venv\Scripts\activate              # Windows
pip install -r scripts/requirements.txt

python scripts/fetch_data.py        # daily, ~75s for 25 symbols
python scripts/backtest.py          # after any signal change
python scripts/walk_forward.py      # periodic monitoring
python scripts/sensitivity.py       # if Params change
python scripts/generate_tearsheet.py  # produces data/tearsheet.png
python scripts/pipeline.py          # rebuilds docs/index.html for Pages
```

### Local preview of the dashboard

```
python scripts/pipeline.py
npx serve docs                       # then open http://localhost:3000
```

Or open `template.html` directly — it falls back to fetching
`data/dashboard_data.json` so it works standalone for development.

On Windows the scripts print Unicode arrows; set `PYTHONIOENCODING=utf-8`
to avoid `cp1252` console errors.

---

## Hygiene rules enforced

- **No look-ahead:** all signals are observed at close T and traded at
  close T + 1. The momentum, breadth, trend-entry and trend-exit signals
  all respect this lag.
- **Survivorship — partial, and under caveat:** the rolling liquidity gate
  uses only ex-ante information (history length, trailing ADV), so it is
  survivorship-free *within* the 25-name set, and the two most violent deaths
  (LUNA −100 %, FTT −75 % worst single day, honestly captured) are in the
  candidate pool. But the 25 names are a hindsight-selected fixed list; the long
  tail of era-liquid coins that later died is absent (2026-07-04 audit). Read the
  headline as an upper bound — see `results/survivorship_audit.md`.
- **No same-bar execution:** rebalance trades realised at the close of the
  rebalance day, on the previous bar's signal.
- **Honest fees:** 10 bps/side on every weight change, including the daily
  trend-exit forced sales.
- **IS/OOS separation:** IS = 2018-01-01 → 2020-12-31, OOS = 2021-01-01
  onwards. Any future parameter search happens on IS only — see
  `scripts/research/vol_target_search.py` for the protocol.
- **Sensitivity verified:** parameter perturbations tested on IS only,
  result reported in `scripts/sensitivity.py`.

---

## What was tested and explicitly rejected

- **Vol-target overlay.** IS Sharpe improved (0.97 vs 0.82, +0.15) on the
  IS-best config (`vol_target=40 %, lookback=15 d`). OOS Sharpe degraded
  (1.05 vs 1.20, −0.16). The relationship between IS and OOS Sharpe across
  the grid was *inverted*. Honest IS-only protocol rejected the overlay.
  See `scripts/research/vol_target_search.py`.

---

## Automation — daily check + email alerts

`scripts/notify.py` + `.github/workflows/daily-check.yml` run on a daily
cron (00:45 UTC) and email a stylised trade alert whenever a new
rebalance or daily exit fires. Each email explains the trade in plain
text + HTML and links back to the live dashboard's Signal Explorer with
the relevant coin pre-selected.

**Data source for the cron is Binance's public market-data mirror.**
`api.binance.com` geo-blocks GitHub Actions' US-based runners (HTTP 451),
but Binance's market-data host `data-api.binance.vision` does not. The
cron uses `scripts/fetch_daily_update.py`, which loads the existing
parquet, fetches only the missing tail of closed daily candles from the
mirror (same Binance USDT-spot substrate as the frozen history), and
appends. No API key is required and there is no per-minute rate cap at
our 25-coin cadence. The full 8-year Binance history in the parquet is
untouched. For local development, `scripts/fetch_data.py` is still the
canonical full-history bootstrap path.

This replaced CryptoCompare on 2026-07-14. CryptoCompare's free histoday
tier is rate-limited (~11 calls/min) and its key exhausted, freezing the
parquet for ten days while CI reported green (the fetch step was
`continue-on-error`). The mirror is the same substrate, needs no key, and
runs in ~20s; a genuine fetch failure now turns the CI run red via the
"Fail if fetch broke" gate while still sending the digest's staleness
banner. EOS and MATIC were rebranded on Binance (POL / Vaulta), so their
legacy `*USDT` pairs return no data; both are outside the investable set
and are frozen (`DELISTED_ON_BINANCE` in the fetch script) pending the
Phase-2 survivorship audit.

### Required and optional GitHub repository secrets

Add at GitHub → Settings → Secrets and variables → Actions → New
repository secret.

**None required for the data fetch.** The Binance data-vision mirror needs
no API key. (The former `CRYPTOCOMPARE_API_KEY` secret is no longer read by
any script and can be deleted from the repo settings.)

**Optional — controls the email alert path. If unset, trade events are still marked as seen so the backlog does not pile up.**

| Secret | Value |
|---|---|
| `EMAIL_FROM` | the sending address, e.g. `phuazz@gmail.com` |
| `EMAIL_TO` | recipient (typically the same as `EMAIL_FROM`) |
| `EMAIL_PASSWORD` | **Gmail App Password** — generate at <https://myaccount.google.com/apppasswords> (2FA must be on). NOT your account password. |
| `EMAIL_SMTP_HOST` | `smtp.gmail.com` for Gmail (defaults to this if unset) |
| `EMAIL_SMTP_PORT` | `587` (defaults to this if unset) |

If any of the email secrets are missing, the workflow still runs cleanly —
trade events get marked as "seen" so the alert backlog does not pile up,
and you can flip on the secrets later without getting bombarded.

The state file `data/last_alert_state.json` tracks which events have
already been alerted on, so the cron is fully idempotent.

To trigger a run manually: GitHub repo → Actions tab → Daily signal
check → Run workflow.

## Known open work (priority order)

1. **2018 bear whipsaw fix.** Strict entry filter buys false bounces in a
   3-coin universe. Candidate structural fix: require a minimum number of
   trend-qualifiers before any allocation (e.g. ≥ 5), else hold cash.
2. **2024–2025 weak-patch post-mortem.** Per-coin / per-month attribution
   to determine whether the underperformance is structural (BTC-dominance
   regime that will mean-revert) or signal-degradation.
3. **Entry-point overlay redesign.** Current implementation is daily-
   overlapping with a 54 % trigger rate. Non-overlapping monthly
   observations with a conditional trigger would make it informative.
4. **Daily exit at close still has 1-bar lag.** Intraday execution would
   change the deployment story materially. Not a backtest change — a
   research project of its own.
5. **Walk-forward with re-fitting.** Current walk-forward is year-by-year
   forward Sharpe; no params are re-selected. A proper expand-window
   walk-forward with annual re-fit would test parameter stability.

---

*Strategy spec last updated: 2026-05-30 (v3.1 — walk-forward-validated parameter change to top-4 + (30, 90, 180) lookbacks).*
