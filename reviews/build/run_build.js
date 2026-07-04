// Rebuild the 2026-07-04 review .docx from the committed content specs.
// Requires the research-review skill installed and the global `docx` package:
//   npm install -g docx ; export NODE_PATH="$(npm root -g)"
// Charts come from ../charts (committed; regenerate with
//   python scripts/research/make_review_charts.py).
const path = require("path");
const { buildReport } = require("C:/Users/phuaz/.claude/skills/research-review/assets/report_builder.js");
const REV = path.resolve(__dirname, "..");   // reviews/
const jobs = [
  [path.join(__dirname, "build_technical.js"), path.join(REV, "2026-07-04_phaseB-C3-C4_v3.1-robustness-review.docx")],
  [path.join(__dirname, "build_summary.js"),   path.join(REV, "2026-07-04_phaseB-C3-C4_v3.1-robustness-review_summary.docx")],
];
(async () => {
  const only = process.argv[2]; // 'technical' | 'summary' | undefined(all)
  for (const [spec, out] of jobs) {
    if (only && !spec.includes(only)) continue;
    try {
      const r = await buildReport(require(spec), out);
      console.log("wrote", r.outPath, r.bytes, "bytes");
    } catch (e) {
      console.error("ERR building", spec, "::", e && e.message);
      process.exit(1);
    }
  }
})();
