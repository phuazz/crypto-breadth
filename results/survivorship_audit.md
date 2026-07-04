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
   today; FTT falls ~$22 → ~$2.3 over the FTX collapse (worst 1-day −75%), residual
   ~$0.23. Both `collapse_captured = True`. So the strategy IS stress-tested against
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
