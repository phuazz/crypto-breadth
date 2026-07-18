# Survivorship & data audit — Phase 2 (2026-07-04)

Reproducible via `scripts/research/survivorship_audit.py` → `results/survivorship_audit.json`.
Substrate: `data/prices.parquet` (Binance USDT spot, 2018-01-01 → 2026-07-03, 25 symbols).

## Headline

The review substrate is **usable** — prices are genuine, the deaths are honestly
captured, and the Binance→CryptoCompare handover shows no discontinuity. But the
universe is a **fixed, hindsight-selected 25-coin list, not a point-in-time
construction**, so the headline v3.1 figures carry a **material, optimistic
survivorship-selection bias** and must be read as an **upper bound**. This does not
halt the review; it attaches a standing caveat that the Phase-B verdict must carry.

## Findings

1. **No true delistings in the set.** All 25 names run to the panel end; not one
   stops trading. A genuine point-in-time universe would contain names that died and
   dropped out. `ends_early = []` ⇒ the set was assembled with hindsight.

2. **Selection gap is large and one-directional.** Of 40 well-known Binance USDT
   coins that carried real liquidity in 2019–2021, **40 of 40 are absent** (VET,
   THETA, XTZ, XMR, DASH, ZEC, NEO, WAVES, OMG, ICX, SAND, MANA, AXS, GALA, SUSHI,
   YFI, CRV, COMP, SNX, GRT, HBAR, RUNE, CAKE, LUNC, UST, SRM, … — indicative, not
   exhaustive). The candidate pool is ~25 versus a true era-liquid pool of ≥ 65, so
   it captures **under 40%** of the era's liquid names. The omission concentrates in
   the alt-heavy years (2019, 2021) — exactly where the strategy posts its best
   numbers, and exactly where the missing pumped-then-died coins would have added
   momentum-chasing losses a survivor set never sees. Direction: **optimistic**.

3. **The two deaths that ARE included are honest, not fabricated.** LUNA craters
   from ~$82 to ~$0.0003 (worst 1-day −100%) on 2022-05-12 then trades residually to
   today *(corrected 2026-07-18 — the "residual" rows were a reassigned-ticker
   splice, since purged; see addendum)*; FTT falls ~$22 → ~$2.3 over the FTX
   collapse (worst 1-day −75%), residual ~$0.23. Both `collapse_captured = True`. So the strategy IS stress-tested against
   the two most violent majors' deaths (mitigant), and the residual near-zero prices
   are kept out of the book by the $25 M ADV liquidity gate most of the time.

4. **Splice is clean.** The only days with ≥ 6 coins moving > 25% are real market
   events (2020-03-12 COVID; 2021-05-19 / 2021-05-24 China-ban crash; 2025-10-10) —
   `unexplained_clusters = []`. BTC crosses the late-May/June-2026 Binance→
   CryptoCompare handover smoothly (no level jump). The review history is a
   trustworthy single-source series. No non-positive closes.

5. **Interior data gaps (handled, but noted).** Post-listing NaN holes: MATIC 569
   (the Sept-2024 MATIC→POL rename), EOS 311, FTT 310, LUNA 17. The investability
   mask treats NaN as not-investable, so gaps exclude the coin rather than injecting
   bad data — but the effective universe is often < 25 (median names with data per
   year: 2018→10, 2019→14, 2020→17, 2021-24 →24-25, 2026→24), and before the
   liquidity gate is even applied.

## Quantification & limits

The bias cannot be point-estimated without the full Binance USDT listing history
(the no-new-vendor rule puts that out of scope — see `DATA_INTEGRITY_POLICY.md` §3).
It is therefore recorded as a **direction (optimistic) + a rough magnitude (candidate
pool < 40% of the era-liquid set, gap concentrated in 2019/2021)**, not a correction
factor. Phase B reads every headline metric as an upper bound and weighs this beside
the deflated-Sharpe and walk-forward evidence.

## Actions taken

- README survivorship claims corrected (two locations) to match this audit.
- Standing caveat carried into the Phase-B verdict (PR-1).
- No data purchased, no series altered (no-new-vendor rule honoured).

---

## Addendum 2026-07-18 — LUNA ticker reassignment: found, measured, purged

**Discovery.** An owner question ("is LUNA still alive?") exposed that the LUNA
column was a three-identity chimera, worse than the nuance recorded in
`DATA_INTEGRITY_POLICY.md` §4 at review time:

1. **Terra Classic**, 2020-08-21 → 2022-05-13 — the genuine death ($77 →
   $0.00005). The strategy's 37 LUNA trades all pre-date it; the last exit was
   2022-04-12, a month before the collapse.
2. **Terra 2.0** from the 2022-05-31 Binance relist — a different asset under the
   same pair, a +177,399% pseudo-return across the 18-day halt gap.
3. The **June-2026 vendor handover** flipped the column back to LUNC-level prices
   (~$0.00006), and the Binance mirror flipped it to 2.0 again on **2026-07-05**
   (+76,222% in one day) — two further identity flips inside live lookback
   windows. This audit's splice test (≥6-coin same-day clusters, BTC continuity)
   is blind to single-coin identity flips **by construction** — a recorded
   limitation of the method.

**Materiality — measured, not argued.** The chimera sat in the investability mask
for 229 days after the death (and 4 days in 2026), so it could touch breadth. A
full counterfactual (post-death LUNA rows removed) versus baseline, run before any
surgery: the tier changed on **5 days in 8.5 years, none a rebalance Monday**, and
the equity curve was **bit-identical (max |diff| = 0.0)** under both v3.1
(`single_name_cap=None`; sanity: Sharpe 1.3483, MaxDD −44.78%) and v3.2 (1.3590,
−39.49%). No filed or displayed number depends on the impostor rows.

**Action (owner-approved 2026-07-18).** The 1,509 post-death LUNA rows were purged
from `data/prices.parquet`; the ticker is hard-frozen in
`scripts/fetch_daily_update.py` (skip-before-request — the pair trades live, so
the tolerate-when-empty freeze used for EOS/MATIC would not have held);
`scripts/test_backtest.py` pins the three frozen last-dates in the daily CI
(LUNA 2022-05-13, EOS/MATIC 2026-07-04); the pipeline exempts frozen tickers from
the staleness badge and surfaces them separately. Post-purge equity re-verified
bit-identical to the pre-purge baseline in-memory on both engines.

**Classification.** Not a registered trial: no parameter changed and the result is
outcome-invariant data hygiene. The "no series altered" line above was true as of
2026-07-04; this addendum records the first deliberate series alteration —
removal of foreign-asset rows, no new vendor involved, measured neutral before
execution. The LUNA column now honestly reads as what it always was economically:
a major that died, and stayed dead.
