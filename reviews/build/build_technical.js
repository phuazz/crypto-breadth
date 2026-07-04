const CH = "C:/dev/Crypto-Breadth/reviews/charts";
module.exports = {
  meta: {
    title: "Crypto-Breadth v3.1 — robustness review",
    subtitle: "Deflated Sharpe · full-config walk-forward · cost stress · weak-patch autopsy · concentration & gate ablations",
    dateISO: "2026-07-04",
    headerLeft: "Crypto-Breadth v3.1 robustness review",
    headerRight: "Personal research — findings record",
    assetsDir: CH,
    metaLeftW: 2500,
  },
  metaTable: [
    ["Project / context", "Crypto-Breadth (Personal research); public repo phuazz/crypto-breadth"],
    ["Review scope", "Deployed-to-repo v3.1 (Binance USDT 25-coin cross-sectional momentum + breadth gate + per-coin trend filters): Phase B (deflated Sharpe, full-config walk-forward, cost/execution stress, weak-patch autopsy, C.2 vol-target overlay), C.3 concentration, C.4 gate ablation"],
    ["Evaluation window", "2018-01-01 → 2026-07-03 daily; IS 2018–2020, OOS 2021+; full-config walk-forward re-fit per anchor year 2020–2026"],
    ["Data basis", "data/prices.parquet (Binance USDT spot), frozen; no new vendor. Substrate carries a survivorship caveat (§3.1)"],
    ["Method basis", "v3.1 engine frozen as the review baseline; realistic costs 10–50 bps/side; rule PR-1 pre-registered before data; deflated-Sharpe haircut over ~50 reconstructed trials"],
    ["Repository commits", "45c2d9a → f816527 (7 commits, 2026-07-04)"],
    ["Running memo", "RESEARCH_MEMO.md (PR-1 + post-hoc amendment; REVIEW CLOSE)"],
    ["Outcome", "REVIEWED — KEEP (amended): edge real, deploy as a small crypto sleeve; original −30%-rule verdict was do-not-deploy"],
  ],
  sections: [
    { type: "h1", text: "1. Executive summary" },
    { type: "numbers", items: [
      [{ text: "KEEP (amended). ", bold: true }, { text: "v3.1's edge is statistically real and beats passive crypto risk-adjusted — full-sample Sharpe 1.35 vs BTC 0.61 and 60/40 0.66; OOS (2021+) Sharpe 1.43. Deploy as a small diversifier sleeve." }],
      [{ text: "Not a multiple-testing fluke. ", bold: true }, { text: "Deflated Sharpe ≥ 0.999 across N = 10–200 reconstructed trials (≈50 base) — the edge clears the noise floor after charging for the search." }],
      [{ text: "Parameters are not overfit. ", bold: true }, { text: "A 60-config annual re-fit (full-config walk-forward) loses only +0.08 Sharpe OOS versus the frozen config; (30,90,180) + top-4 was in-sample-best in all seven anchor years." }],
      [{ text: "Cost-robust. ", bold: true }, { text: "Full Sharpe holds 1.35 → 1.21 from 10 to 50 bps/side; OOS 1.43 → 1.28." }],
      [{ text: "Drawdown is deep but not disqualifying. ", bold: true }, { text: "−44.8% (peak Dec-2024 → trough Jun-2026), far shallower than passive crypto (BTC −81%, 60/40 −85%). The original −30% deploy ceiling was removed post-hoc as an equity frame mis-applied to crypto (§5)." }],
      [{ text: "Three caveats govern sizing, not the decision: ", bold: true }, { text: "a survivorship-optimistic headline (25-coin universe hindsight-selected; 40/40 era-liquid deaths absent), a 2021-concentrated track (+1882% one year), and an on-notice 2025–26 weak patch." }],
      [{ text: "The modification path failed. ", bold: true }, { text: "Concentration (C.3a) hurts; a majors time-series engine (C.3b) is weaker and no shallower. The breadth gate is validated (load-bearing; beats a BTC-200d filter)." }],
    ] },

    { type: "h1", text: "2. Verified architecture" },
    { type: "p", text: "The reviewed engine (scripts/backtest.py, frozen) is a weekly cross-sectional momentum book: composite risk-adjusted momentum over 30/90/180 days, top-4 equal-weight, gated by the share of the investable universe above its 50-day moving average (tiered 0/30/60/100% gross), with a strict per-coin trend-entry filter and an asymmetric daily trend-exit. Signals observed at close T, traded at close T+1; 10 bps/side. The universe is a rolling-liquidity gate over 25 Binance USDT pairs." },
    { type: "callout", text: "Drift from the written record (code beats docs): the README claimed the rolling gate 'removes the survivorship bias of any hand-picked fixed list'. It does not — the 25 names are themselves a hindsight-selected set (§3.1). Corrected in the README in this review." },

    { type: "h1", text: "3. Findings" },

    { type: "h2", text: "3.1 Survivorship & data substrate (Phase 2)" },
    { type: "p", text: "The universe is a fixed, hindsight-selected 25-coin list, not a point-in-time construction: no coin ever delists (every name runs to the panel end), and 40 of 40 well-known 2019–2021 Binance USDT era-majors are absent — the candidate pool is under 40% of the true era-liquid set, with the gap concentrated in the alt-heavy years (2019, 2021). The survivorship-selection bias is therefore optimistic and material; it cannot be point-estimated without the full Binance listing (out of scope under the no-new-vendor rule) and is carried as an upper-bound caveat. Mitigants: the two most violent deaths (LUNA worst 1-day −100%, FTT −75%) are honestly captured, the Binance→CryptoCompare handover shows no discontinuity, and interior data gaps are handled by the investability mask." },

    { type: "h2", text: "3.2 Statistical significance — deflated Sharpe" },
    { type: "p", text: "Reconstructed trial count ≈ 50 (sensitivity OAT ~33 + vol-target grid 12 + walk-forward grid 6 + structural ladder ~5, net of overlaps). Trial-Sharpe dispersion from the 60-config grid = 0.124 annual. The deflated Sharpe stays at or above 0.999 for every assumed N from 10 to 200 — the edge is far above the multiple-testing noise floor and is not an artefact of the search. (This is orthogonal to the survivorship bias, which concerns the level, not the significance.)" },
    { type: "chart", file: "dsr_vs_n.png", caption: "Deflated Sharpe (probability the true Sharpe exceeds the search-adjusted threshold) against the assumed number of trials. It stays ≥ 0.999 even at N = 200, well above the 0.95 bar (dashed)." },

    { type: "h2", text: "3.3 Parameter robustness — full-config walk-forward" },
    { type: "p", text: "A 60-config factorial (momentum lookbacks × top-N × breadth-MA window) was re-selected annually on an expanding in-sample window and chained out-of-sample. Re-fitting scores Sharpe 1.357 versus the frozen 1.437 — it loses +0.080, well inside the 0.30 tolerance. (30,90,180) + top-4 was in-sample-best in all seven anchors; only the breadth-MA window wavered (70 in 2020–21, the default 50 thereafter). The parameters are not overfit." },
    { type: "table",
      headers: ["Walk-forward", "OOS Sharpe", "Result"],
      rows: [
        ["Frozen production config", "1.437", "baseline"],
        ["Annual full-config re-fit", "1.357", "loses +0.080 (≤ 0.30 → pass)"],
      ],
      widths: [4026, 2500, 2500], numericFrom: 1 },

    { type: "h2", text: "3.4 Cost & execution stress" },
    { type: "p", text: "The daily trend-exit lifts turnover, so costs matter; the edge nonetheless survives taker-level fees." },
    { type: "table",
      headers: ["Fee (bps/side)", "Full Sharpe", "OOS Sharpe", "Full CAGR"],
      rows: [
        ["10 (maker baseline)", "1.351", "1.429", "75.6%"],
        ["20", "1.315", "1.391", "72.3%"],
        ["30", "1.279", "1.354", "69.2%"],
        ["50 (mid-size taker)", "1.206", "1.280", "63.0%"],
      ],
      widths: [3026, 2000, 2000, 2000], numericFrom: 1 },

    { type: "h2", text: "3.5 Weak-patch autopsy (C.1) — regime, not decay" },
    { type: "p", text: "The strategy made a new all-time high (214×) on 2024-12-02, then drew down −44.8% into 2026-06. In the weak patch the breadth gate is actively de-risking (average gross exposure 0.40 vs 0.63 in a good period; 42% in cash vs 26%), and the momentum score has not inverted (held-vs-eligible forward-5-day spread stays +0.15%). But the edge has thinned ~85% (good-period spread +1.02%, hit-rate 42%): the book needs alt-dispersion — fat-right-tail alt winners — and a BTC-dominated 2025–26 offered none. Diagnosis: a severe regime episode, not signal inversion; the strategy is on notice. The return profile is dominated by 2021 (+1882%), which is also the most survivorship-inflated year." },
    { type: "chart", file: "annual_returns.png", caption: "Annual returns (symmetric-log scale so every year is legible). 2021's +1882% dominates the whole track; strip it and the deployable edge is far more pedestrian." },

    { type: "h2", text: "3.6 Risk profile versus passive crypto — the deployability case" },
    { type: "p", text: "Judged against the correct benchmark — other crypto exposures, not equities — v3.1 dominates passive crypto on both risk-adjusted return and drawdown. This is the basis for the amended KEEP." },
    { type: "table",
      headers: ["Series", "Full Sharpe", "Max drawdown"],
      rows: [
        ["Strategy v3.1", "1.35", "−44.8%"],
        ["BTC buy-and-hold", "0.61", "−81.2%"],
        ["60/40 BTC-ETH", "0.66", "−85.5%"],
      ],
      widths: [4026, 2500, 2500], numericFrom: 1 },
    { type: "chart", file: "equity_vs_passive.png", caption: "Growth of $1 (log scale). The strategy compounds far above BTC and 60/40 over the full sample; the outperformance is concentrated in the alt years." },
    { type: "chart", file: "drawdown_vs_btc.png", caption: "Drawdown. The strategy's worst is −45% versus BTC's −81%; deep in absolute terms but roughly half of passive crypto. The dashed line marks the removed −30% ceiling." },

    { type: "h2", text: "3.7 C.2 vol-target overlay (registered arm)" },
    { type: "p", text: "The risk-overlay-lab round-1 winner (EWMA estimator, 0.10 band, cap 1.0, weekly) transferred onto the crypto book trims the tail but does not transform it: MaxDD reaches −33% at best (target 30%, at a Sharpe cost to 1.04) and −35 to −38% at higher targets. The equity-book overlay does not fully transfer because the crypto tail (excess kurtosis 17.5, single-day gaps) outruns a lagged vol estimate. Overlay = optional tail-trimming, not a structural fix." },

    { type: "h2", text: "3.8 C.3 concentration (Phase 4) — rejected" },
    { type: "p", text: "Neither concentration arm produces a better book. Shrinking the eligible pool by trailing ADV monotonically degrades the cross-sectional engine (Sharpe 1.35 at 25 names → 0.84 at 4; drawdown worsens) — starving the ranker of breadth starves the signal. A new majors time-series-momentum engine (BTC+ETH / top-5-ADV × MA / MA-rising / 12−1 TSMOM × equal-weight / vol-target) is lower-Sharpe on every variant (best 0.87) and no shallower (best −50.6%); the deep drawdowns are genuine single-day gap risk (worst −74% = the COVID crash), which a weekly long-only filter cannot dodge. No deployable candidate emerged." },

    { type: "h2", text: "3.9 C.4 gate ablation (Phase 5) — validated" },
    { type: "p", text: "The gate is the single most important component: removing it collapses Sharpe by 0.61 (1.35 → 0.74) and blows the drawdown out by 38 points (−45% → −83%). The breadth gate beats a simple BTC-200-day filter full-sample (+0.17 Sharpe, shallower drawdown), so it earns its complexity. Two honest caveats: the tiering is redundant (a binary breadth gate matches the graduated one), and in the 2024+ weak patch the BTC filters edged breadth — the gate's superiority is regime-dependent." },

    { type: "h1", text: "4. Findings summary", pageBreakBefore: true },
    { type: "table",
      headers: ["Component / test", "Result", "Bearing on the verdict"],
      rows: [
        ["Deflated Sharpe (N 10–200)", "≥ 0.999", "Edge is real (not a search artefact)"],
        ["Full-config walk-forward", "loses +0.08 OOS", "Parameters not overfit"],
        ["Cost stress (10–50 bps)", "Sharpe 1.35 → 1.21", "Cost-robust"],
        ["Weak-patch autopsy", "regime, on notice", "Score not inverted; on watch"],
        ["Risk vs passive crypto", "Sharpe 1.35 vs 0.61", "Deployment case"],
        ["C.2 vol-target overlay", "MaxDD → −33% best", "Optional tail-trim"],
        ["C.3a concentration", "monotonically worse", "Rejected"],
        ["C.3b majors engine", "weaker, −50%+", "Rejected"],
        ["C.4 breadth gate", "load-bearing; beats BTC-200d", "Validated"],
      ],
      widths: [3226, 2900, 2900], numericFrom: 99 },

    { type: "h1", text: "5. Pre-registration amendment (the −30% ceiling)" },
    { type: "p", runs: [
      { text: "The pre-registered rule (PR-1) set a hard −30% MaxDD deployment ceiling. On the results this proved binding — v3.1 (−44.8%) and every overlay / concentration / majors variant fail it. At the owner's instruction the ceiling was ", },
      { text: "removed post-hoc", bold: true },
      { text: " and logged transparently (not a silent rewrite) to preserve pre-registration integrity. Rationale: −30% imported an equity-style drawdown frame onto a crypto book; the correct benchmark is passive crypto (BTC −81%, 60/40 −85%), against which −44.8% is far shallower. Effect: under the ", },
      { text: "original frozen rule the verdict was MODIFY / do-not-deploy", italics: true },
      { text: "; under the amended rule it is ", },
      { text: "KEEP / deployable as a small sleeve", bold: true },
      { text: ". Both are on the record. The 0.30 OOS Sharpe-loss tolerance is unchanged." },
    ] },

    { type: "h1", text: "6. Decisions" },
    { type: "table",
      headers: ["Component", "Decision", "Basis"],
      rows: [
        ["v3.1 strategy (as-is)", "KEEP — deploy small", "Real edge; beats passive crypto; caveats govern sizing"],
        ["Breadth gate", "KEEP", "Load-bearing; beats BTC-200d filter"],
        ["Gate tiering (0/30/60/100)", "SIMPLIFY (optional)", "Binary gate matches the graduated one"],
        ["C.2 vol-target overlay", "OPTIONAL", "Trims tail to ~−33%; does not transform"],
        ["C.3a concentration", "REJECT", "Monotonically degrades the ranker"],
        ["C.3b majors engine", "REJECT", "Weaker and no shallower"],
        ["−30% MaxDD ceiling", "REMOVED (amended)", "Equity frame mis-applied to crypto"],
        ["Deployment sizing", "FLAGGED", "Small sleeve; survivorship + 2021 concentration + on-notice"],
      ],
      widths: [2926, 2500, 3600] },

    { type: "h1", text: "7. Trial register" },
    { type: "p", text: "95 configurations were evaluated across the arms and logged in results/trial_registry.jsonl (99 rows including references): Phase-B full-config grid 60, C.2 overlay 5, C.3a shrink 5, C.3b majors engine 20, C.4 gate 5. None was selected by an out-of-sample metric; the deflated-Sharpe haircut in §3.2 charges for the search. No production parameter was changed by the review." },
    { type: "chart", file: "scope_funnel.png", caption: "Configurations evaluated per arm. 95 tested → v3.1 kept unchanged, C.3 rejected, the gate validated. Rigour with restraint: the search did not move the production parameters." },

    { type: "h1", text: "8. Artefact register" },
    { type: "bullets", items: [
      "Harnesses: scripts/research/phase_b_review.py, phase_c3_concentration.py, phase_c4_gate.py, survivorship_audit.py, make_review_charts.py (all committed).",
      "Results: results/phase_b_review.{json,md}, phase_c3_concentration.{json,md}, phase_c4_gate.{json,md}, survivorship_audit.{json,md}, trial_registry.jsonl.",
      "Tests: tests/ (19 pytest engine unit tests); CI .github/workflows/tests.yml.",
      "Memo & policy: RESEARCH_MEMO.md, DATA_INTEGRITY_POLICY.md, CLAUDE.md.",
      "Repository: phuazz/crypto-breadth, commits 45c2d9a → f816527. Dashboard: phuazz.github.io/crypto-breadth.",
    ] },

    { type: "h1", text: "9. Next phase" },
    { type: "bullets", items: [
      "If deployed: a small crypto diversifier sleeve, sized so a −45% book move is tolerable at the portfolio level.",
      "C.5 (perpetual-futures short / hedge) — parked; the only mechanism that can materially cut the crypto tail, for a larger allocation.",
      "Optional: apply the C.2 vol-target overlay for tail-trimming; simplify the gate to binary.",
      "Truly untouched out-of-sample begins 2027-01-01.",
    ] },
  ],
  signoff: [
    ["Prepared by", "Claude Code research session, under direction of Zhenghao Phua"],
    ["Reviewed and approved by", ""],
    ["Date", ""],
    ["Next review", "On material regime change, or before any capital deployment"],
  ],
  disclaimer: "Personal research artefact. All performance figures are simulated backtests, net of stated costs, and carry a documented optimistic survivorship-selection bias; nothing here is investment advice.",
};
