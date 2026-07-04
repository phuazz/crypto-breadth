const CH = "C:/dev/Crypto-Breadth/reviews/charts";
module.exports = {
  meta: {
    title: "Crypto-Breadth v3.1 — what the review found",
    subtitle: "Plain-language summary for allocation review",
    dateISO: "2026-07-04",
    headerLeft: "Crypto-Breadth v3.1 — plain-language summary",
    headerRight: "Personal research",
    assetsDir: CH,
    metaLeftW: 2600,
  },
  metaTable: [
    ["In one sentence", "This crypto strategy has a genuine edge and beats simply holding Bitcoin — but its gains lean heavily on one boom year and it can still lose about 45%, so it belongs as a small, eyes-open sleeve, not a core holding."],
    ["Decision", "KEEP — deploy small. (An earlier drawdown limit that would have blocked it was judged wrong for crypto and removed; that is on the record.)"],
  ],
  sections: [
    { type: "h1", text: "1. The questions, and the answers" },
    { type: "table",
      headers: ["The question", "The answer", "How it was tested"],
      rows: [
        ["Is the edge real, or luck from trying many versions?", "Real — it survives a penalty for the ~50 versions tried.", "Deflated Sharpe (edge after charging for the search)"],
        ["Would we have picked these settings in advance?", "Yes — re-choosing them each year barely changes the result.", "Expanding-window walk-forward, 60 settings"],
        ["Does it beat just holding crypto?", "Yes — better return per unit of risk, and half the drawdown.", "Versus Bitcoin and a 60/40 Bitcoin-Ether mix, after fees"],
        ["What is the catch?", "One year (2021) does most of the work, the test universe flatters it, and it is in a bad patch now.", "Universe audit + year-by-year + recent-patch autopsy"],
      ],
      widths: [3026, 3000, 3000], numericFrom: 99 },
    { type: "callout", text: "Terms: 'risk-adjusted return' = return per unit of price swings, higher is better (Bitcoin ≈ 0.6, this strategy ≈ 1.35). 'Drawdown' = worst peak-to-trough loss. 'Sleeve' = a small book within a larger portfolio." },

    { type: "h1", text: "2. What we found" },

    { type: "h2", text: "2.1 It beats simply holding crypto" },
    { type: "p", text: "Over 2018–2026 the strategy compounded far above Bitcoin and a 60/40 Bitcoin-Ether mix, at a better return-per-risk (1.35 versus 0.6)." },
    { type: "chart", file: "equity_vs_passive.png", caption: "Growth of $1, log scale (each gridline is 10×). The dark line is the strategy; red is Bitcoin, teal is 60/40. Higher is better." },

    { type: "h2", text: "2.2 Its worst loss is about half of Bitcoin's" },
    { type: "p", text: "The strategy's deepest fall is about −45%, versus roughly −81% for holding Bitcoin — deep in absolute terms, but far shallower than passive crypto." },
    { type: "chart", file: "drawdown_vs_btc.png", caption: "How far below the previous high each sits, over time. Shallower (closer to zero) is better; the strategy (dark) stays well above Bitcoin (red)." },

    { type: "h2", text: "2.3 But one boom year does most of the work" },
    { type: "p", text: "2021 returned about +1882% and dominates the whole record; strip it out and the edge is far more ordinary — a reason to size the position modestly." },
    { type: "chart", file: "annual_returns.png", caption: "Return each calendar year (compressed scale so small and huge years both show). One year dwarfs the rest." },

    { type: "h2", text: "2.4 The edge is not a fluke of trying many versions" },
    { type: "p", text: "After charging for the roughly fifty strategy versions explored, the edge is still overwhelmingly likely to be real (well above the usual confidence bar)." },
    { type: "chart", file: "dsr_vs_n.png", caption: "Confidence the edge is real (vertical) as we assume more versions were tried (horizontal). It stays near-certain even at 200; the dashed line is the usual 95% bar." },

    { type: "h1", text: "3. The work behind this summary", pageBreakBefore: true },
    { type: "p", text: "Ninety-five strategy versions were tested across five lines of attack. None changed the production settings; two proposed 'improvements' (concentrating the coins, and a Bitcoin-Ether-only trend engine) were tested and rejected, and the core risk filter was confirmed as the most important part." },
    { type: "chart", file: "scope_funnel.png", caption: "Versions tested per line of attack. The point is restraint: a large search did not move the live settings, and the two candidate changes did not survive." },
    { type: "p", runs: [
      { text: "Full detail and every figure sit in the technical record ", },
      { text: "(2026-07-04_phaseB-C3-C4_v3.1-robustness-review.docx)", italics: true },
      { text: " and the public repository / dashboard (phuazz.github.io/crypto-breadth)." },
    ] },
  ],
  disclaimer: "Personal research artefact. All figures are simulated backtests, net of fees, and carry a documented optimistic bias (the test universe excludes coins that later died); nothing here is investment advice.",
};
