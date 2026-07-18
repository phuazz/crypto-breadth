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
| Binance USDT spot OHLCV (`api.binance.com`) | THE research substrate — all backtest history | `data/prices.parquet`, bootstrapped locally via `scripts/fetch_data.py` (ccxt) | Returns HTTP 451 to US GitHub runners → cannot refresh from CI. Delists / rebrands pairs (survivorship risk — §3). The full ~8-year history exists ONLY in the parquet. |
| Binance market-data mirror (`data-api.binance.vision`) | OPERATIONAL — daily tail append for the alert cron (since 2026-07-14) | `scripts/fetch_daily_update.py` | Same exchange and substrate as the research history (NOT a cross-vendor splice — §4). Not geo-restricted; no API key; no per-minute cap at our cadence. Appends only closed daily candles. EOS/MATIC rebranded (POL / Vaulta) → legacy pairs return no data, frozen as `DELISTED_ON_BINANCE` (non-investable; Phase-2 audit). |
| ~~CryptoCompare histoday~~ (retired 2026-07-14) | Former operational tail | — | Retired: free histoday behind auth since June 2026, key rate-limited (~11 calls/min) and exhausted, freezing the parquet for ten days. Replaced by the Binance mirror above. Historical parquet rows appended by this path pre-2026-07-14 remain subject to the §4 splice guard. |

## 3. Survivorship & the point-in-time universe

- The universe MUST be point-in-time INCLUDING delisted / deprecated pairs (LUNA / FTT
  era).
- The rolling-liquidity gate (`investability_mask_liquidity`) is ex-ante by
  construction, but it can only reinstate coins that are IN the parquet. If delisted
  pairs were never captured, no gate recovers them.
- **Phase 2 audits this.** If gaps are found: document + quantify the bias direction
  and rough magnitude; do NOT buy data (no-new-vendor rule); do NOT leave the README
  "removes the survivorship bias of any hand-picked fixed list" claim unqualified.

## 4. The CryptoCompare ↔ Binance splice (legacy — tail source now Binance-native)

- Research history (Phase B and all C-arms) is **Binance-parquet-only**.
- As of 2026-07-14 the operational cron appends a **Binance** tail
  (`data-api.binance.vision`, same exchange as the substrate), so the daily tail is no
  longer a cross-vendor splice.
- **Legacy guard (still applies to pre-2026-07-14 rows):** the retired CryptoCompare
  cron appended a different-venue tail from ~when CI started through 2026-07-13. Before
  trusting any historical number, verify (a) which rows are Binance versus
  CryptoCompare, and (b) there is no level discontinuity at that splice. If it has
  contaminated pre-2026 history, treat it as a failure mode and quarantine the
  CryptoCompare rows from the review substrate.
- **Frozen tickers (rebrands / ticker reassignments) — LUNA resolved 2026-07-18:**
  EOS (→A/Vaulta) and MATIC (→POL) rebranded; their legacy pairs return no data and
  froze at 2026-07-04. LUNA was the sharp case: the ticker was REASSIGNED — Terra
  Classic died 2022-05-13, and Binance serves Terra 2.0 (a different asset) under
  `LUNAUSDT` — so the column had absorbed three identity flips (Classic → 2.0 at the
  2022-05-31 relist, a +177,399% pseudo-return; back to LUNC-level prices at the
  June-2026 vendor handover; to 2.0 again on 2026-07-05, +76,222% in a day). On
  2026-07-18 the 1,509 post-death rows were purged and the ticker hard-frozen in the
  fetch script (skip-before-request — the pair trades live, so tolerate-when-empty
  is not a freeze). Counterfactual measured before surgery: the chimera was in the
  investability mask 229 days post-death but was never held and never top-4; the
  tier differed on 5 non-rebalance days only; equity bit-identical (max |diff| 0.0)
  under both v3.1 and v3.2. Not a registered trial — outcome-invariant data hygiene.
  Guard: `scripts/test_backtest.py` pins all three frozen last-dates
  (LUNA 2022-05-13, EOS / MATIC 2026-07-04) in the daily CI. Frozen tickers are
  exempt from the staleness badge and surfaced separately as "frozen". Full record:
  `results/survivorship_audit.md`, 2026-07-18 addendum. The EOS / MATIC successor-
  pair remap (POLUSDT / AUSDT) remains the open Phase-2 item.

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
