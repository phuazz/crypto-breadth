# crypto-breadth

A breadth-gate + ranked-momentum strategy for crypto majors on Binance USDT
spot. Research-grade — not deployed.

**Live dashboard:** [phuazz.github.io/crypto-breadth](https://phuazz.github.io/crypto-breadth/)
— interactive Plotly equity curve, drawdown, regime breakdown, bootstrap
Sharpe distribution and parameter sensitivity. Auto-generated from
`scripts/pipeline.py`.

Current version: **v3.1**. The strategy, the file layout, and the honest
caveats are all below. For the full session history that produced this, see
the commit log. v3.1 adopted top-4 + (30, 90, 180) momentum lookbacks after
an expand-window walk-forward picked that config in all seven annual
re-fits — see `scripts/walk_forward_refit.py`.

---

## What the strategy does (v3)

- **Universe:** 25 USDT pairs on Binance, with a **rolling liquidity gate** —
  a coin is investable on date T only if it has ≥ 90 days of history and its
  trailing 30-day average daily $ volume is ≥ $25 M. This removes the
  survivorship bias of any hand-picked fixed list.
- **Breadth gate:** % of investable universe trading above its 50 d MA, mapped
  to tiered gross exposure (0 / 30 / 60 / 100 %). The tier graduation
  contributes almost nothing — a binary (0, 0, 0, 1) gate gives essentially
  the same Sharpe — but the gate itself is structurally important
  (gate ablation: +0.25 Sharpe, +30 pp MaxDD improvement).
- **Sizing:** composite momentum score (risk-adjusted returns over 30, 90 and
  180 d), top-4 equal-weight when on. Adopted in v3.1 after an expand-window
  walk-forward picked this config in all seven annual re-fits (2020 → 2026).
- **Trend entry filter:** a coin can only enter the top-N rank if close > own
  50 d MA AND the MA is rising. Strict — designed to avoid head-fakes.
- **Trend exit filter:** asymmetric — close < own 50 d MA triggers a forced
  sell at the **next** close (1-bar lag). Looser than the entry rule, to
  avoid whipsawing on normal pullbacks.
- **Cadence:** weekly Monday rebalance + daily exit overrides. Signal observed
  at close T, traded at close T + 1.
- **Fees:** 10 bps per side (Binance VIP-0 spot).

---

## Performance (full sample 2018-01-01 → 2026-05-28)

| series | CAGR | Sharpe | MaxDD |
|---|---|---|---|
| **strategy (v3.1)** | **77.5 %** | **1.37** | **−42.5 %** |
| BTC HODL | 22.4 % | 0.64 | −81.2 % |
| equal-weight investable | 17.1 % | 0.60 | −82.8 % |
| 60/40 BTC/ETH | 23.5 % | 0.66 | −85.5 % |

**Out-of-sample (2021-01-01 → today):** strategy CAGR 91.3 %, Sharpe 1.45,
MaxDD −42.5 %.

**Block-bootstrap 90 % CI on full-sample Sharpe:** [0.82, 1.92].
P(Sharpe > 0) = 100 %. P(Sharpe > 0.8) = 95.5 %.

**Parameter sensitivity (IS only, OAT ±50 %):** 6 of 7 parameters ROBUST or
MILDLY SENSITIVE. The 7th (`per_coin_trend_window`) is fragile in IS but
stable in OOS. No parameter's default sits at the IS peak — no evidence of
implicit IS-tuning.

---

## What this strategy is not

Three things are important to say upfront, because the headline numbers
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
- **No survivorship in the universe:** rolling liquidity gate uses only
  ex-ante information (history length, trailing ADV). Failed coins
  (LUNA, FTT) are in the candidate pool and contribute to the backtest
  through their decline.
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
