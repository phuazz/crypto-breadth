# DATA_INTEGRITY_POLICY.md — Crypto-Breadth

Layers on the vault data-integrity rules (`C:\dev\CLAUDE.md`). Governs the data
substrate for every backtest and for the daily operational cron.

## 1. Scope

Research-grade crypto breadth / momentum strategy (v3.1), no capital deployed. All
historical performance rests on a single substrate (§2). This policy fixes what is
trusted for research versus what is operational-only, and the guards that keep the two
from contaminating each other.

## 2. Data-sources catalogue

| Source | Used for | Endpoint / path | Known issues & rules |
|---|---|---|---|
| Binance USDT spot OHLCV | THE research substrate — all backtest history | `data/prices.parquet`, bootstrapped locally via `scripts/fetch_data.py` (ccxt) | Binance returns HTTP 451 to US GitHub runners → cannot refresh from CI. Delists pairs (survivorship risk — §3). The full ~8-year history exists ONLY in the parquet. |
| CryptoCompare histoday | OPERATIONAL ONLY — daily tail append for the alert cron | `scripts/fetch_daily_update.py` (USDT-quoted) | Free histoday behind auth since June 2026 (needs `CRYPTOCOMPARE_API_KEY`). Different venue than Binance → potential level discontinuity. **Never the review substrate (§4).** |

## 3. Survivorship & the point-in-time universe

- The universe MUST be point-in-time INCLUDING delisted / deprecated pairs (LUNA / FTT
  era).
- The rolling-liquidity gate (`investability_mask_liquidity`) is ex-ante by
  construction, but it can only reinstate coins that are IN the parquet. If delisted
  pairs were never captured, no gate recovers them.
- **Phase 2 audits this.** If gaps are found: document + quantify the bias direction
  and rough magnitude; do NOT buy data (no-new-vendor rule); do NOT leave the README
  "removes the survivorship bias of any hand-picked fixed list" claim unqualified.

## 4. The CryptoCompare ↔ Binance splice

- Research history (Phase B and all C-arms) is **Binance-parquet-only**.
- The operational cron appends a CryptoCompare tail to `prices.parquet` for the alert
  path only.
- **Guard:** before trusting any historical number, verify (a) which rows are Binance
  versus CryptoCompare, and (b) there is no level discontinuity at the splice. If the
  splice has contaminated pre-2026 history, treat it as a failure mode and quarantine
  the CryptoCompare rows from the review substrate.

## 5. Calendar & boundaries

- 24/7 market. All week / month boundaries and rebalance timestamps are defined in
  **UTC**. No weekday assumptions imported from equity code.
- Binance's daily candle closes 00:00 UTC; the cron runs 00:45 UTC.
- NOTE / audit target: `build_target_weights` keys the weekly rebalance off
  `DatetimeIndex.weekday == 0`. Confirm the parquet date index is UTC-aligned so
  "Monday" means the intended UTC Monday (asserted in `tests/`).

## 6. Stablecoin-depeg days

- USDT is the quote asset. On depeg days, dollar-denominated ADV and returns can
  distort. Flag known depeg windows; do not silently treat a depeg-driven move as
  signal.

## 7. Refresh cadence & escalation

- Daily cron (`.github/workflows/daily-check.yml`, 00:45 UTC): fetch the tail, run the
  regression smoke test (`scripts/test_backtest.py`), rebuild the dashboard, email
  alerts. A smoke-test failure BLOCKS the rebuild + commit — the dashboard cannot
  silently publish numbers from a broken backtest.
- Parquet vintage is surfaced on the dashboard provenance line. Escalate if the parquet
  stops updating or the smoke-test bands trip.
