# reviews/build — docx record build scripts

Reproducible build of the 2026-07-04 review records (previously these lived only
in a scratch dir — flagged in the four-lens critique as a reproducibility gap).

- `build_technical.js` — content spec for the technical findings record.
- `build_summary.js` — content spec for the plain-language allocator summary.
- `run_build.js` — runner.

## Rebuild

Charts first (committed under `../charts`; regenerate to refresh):

    python scripts/research/make_review_charts.py

Then the docx (needs the `research-review` skill + the global `docx` package):

    npm install -g docx                 # once
    export NODE_PATH="$(npm root -g)"
    node reviews/build/run_build.js             # both
    node reviews/build/run_build.js technical   # one

Outputs land in `reviews/`. Validate with the docx-skill validator; the engine
enforces house style and verifies the date at build time.

Note: `run_build.js` references the research-review engine at
`~/.claude/skills/research-review/assets/report_builder.js` — adjust if the skill
is installed elsewhere.
