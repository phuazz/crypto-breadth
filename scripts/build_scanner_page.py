"""Inject scanner_latest.json into scanner_template.html -> docs/scanner.html.

Same shape as pipeline.py: the template is the source file and is what gets
edited; docs/scanner.html is generated and never hand-touched. The template
carries a fetch fallback so it also works standalone (`npx serve .` from the
project root).

Deliberately separate from pipeline.py. The scanner is a monitoring page;
coupling it into the dashboard build would let a scanner fault fail the
dashboard, and the premise of the scanner is that it cannot disturb the book.

Usage:
    python scripts/build_scanner_page.py
    python scripts/build_scanner_page.py --check   # verify, write nothing
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "scanner_template.html"
DATA_PATH = ROOT / "data" / "scanner_latest.json"
OUT_PATH = ROOT / "docs" / "scanner.html"
# Fetched by the page when a row is expanded, so it is not injected — but it
# must exist and cover the rows, or clicking a row yields nothing.
HISTORY_PATH = ROOT / "docs" / "scanner_history.json"

PLACEHOLDER_START = "// __SCANNER_DATA_START__"
PLACEHOLDER_END = "// __SCANNER_DATA_END__"
MAX_TEMPLATE_BYTES = 200 * 1024      # vault rule for source files
CONFLICT_MARKERS = ("<<<<<<<", ">>>>>>>")


class ScannerPageError(RuntimeError):
    """Raised when the page cannot be built safely."""


def inject(template_text: str, payload: dict) -> str:
    """Replace the placeholder block with the data as an inline const."""
    start = template_text.find(PLACEHOLDER_START)
    end = template_text.find(PLACEHOLDER_END)
    if start == -1 or end == -1:
        raise ScannerPageError(
            f"placeholder markers missing from {TEMPLATE.name}; expected "
            f"{PLACEHOLDER_START!r} and {PLACEHOLDER_END!r}"
        )
    if end < start:
        raise ScannerPageError("placeholder markers are in the wrong order")

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # "</script>" inside a JSON string would close the host element early.
    body = body.replace("</", "<\\/")
    replacement = (
        f"{PLACEHOLDER_START}\n"
        f"const SCANNER_DATA_INLINE = {body};\n"
        f"{PLACEHOLDER_END}"
    )
    return template_text[:start] + replacement + template_text[end + len(PLACEHOLDER_END):]


def assert_payload_usable(payload: dict) -> None:
    """Second, independent gate on the JSON as it stands on disk."""
    problems: list[str] = []
    rows = payload.get("rows") or []
    if not rows:
        problems.append("no rows")
    if payload.get("n_rows") != len(rows):
        problems.append(f"n_rows {payload.get('n_rows')} != {len(rows)} rows present")
    if not payload.get("as_of"):
        problems.append("no as_of date")

    ranks = sorted(r["rank"] for r in rows if r.get("rank") is not None)
    if ranks and ranks != list(range(1, len(ranks) + 1)):
        problems.append("ranks are not a permutation of 1..n")

    missing_asof = [r["ticker"] for r in rows if not r.get("as_of")]
    if missing_asof:
        problems.append(f"rows without their own as_of: {missing_asof[:5]}")

    frozen = set(payload.get("frozen_excluded") or [])
    leaked = sorted(frozen & {r["ticker"] for r in rows})
    if leaked:
        problems.append(f"frozen tickers published as rows: {leaked}")

    # The unvalidated-parameters statement is a disclosure, not decoration.
    if payload.get("parameters_validated") is not False:
        problems.append("parameters_validated must be false until validation exists")
    if not payload.get("parameter_note"):
        problems.append("parameter_note is missing")

    if problems:
        raise ScannerPageError(
            "scanner_latest.json is not publishable:\n  - " + "\n  - ".join(problems)
        )


def assert_history_covers(payload: dict, history_path: Path = HISTORY_PATH) -> str:
    """Every published row must have a chart series on an existing calendar."""
    if not history_path.exists():
        raise ScannerPageError(
            f"missing {history_path.name} — run `python scripts/run_scanner.py`"
        )
    history = json.loads(history_path.read_text(encoding="utf-8"))
    series = history.get("series") or {}
    calendars = history.get("calendars") or {}

    problems: list[str] = []
    missing = [r["ticker"] for r in payload.get("rows", []) if r["ticker"] not in series]
    if missing:
        problems.append(f"no chart history for: {', '.join(missing[:8])}")
    for ticker, s in series.items():
        axis = calendars.get(s.get("calendar"))
        if axis is None:
            problems.append(f"{ticker}: references missing calendar {s.get('calendar')!r}")
            break
        if len(s.get("close") or []) != len(axis):
            problems.append(
                f"{ticker}: {len(s.get('close') or [])} closes against a "
                f"{len(axis)}-day calendar"
            )
            break
    if problems:
        raise ScannerPageError("chart history is not publishable:\n  - " + "\n  - ".join(problems))
    return (
        f"history {history_path.stat().st_size / 1024:.0f} KB, "
        f"{len(series)} series x {history.get('sessions')} days"
    )


def assert_output_clean(text: str) -> None:
    for marker in CONFLICT_MARKERS:
        if marker in text:
            raise ScannerPageError(f"built page contains a merge conflict marker {marker!r}")
    if "SCANNER_DATA_INLINE = null" in text:
        raise ScannerPageError("injection did not take — data is still null")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true",
                        help="validate inputs and the render, but write nothing")
    args = parser.parse_args(argv)

    if not TEMPLATE.exists():
        raise ScannerPageError(f"missing template: {TEMPLATE}")
    if not DATA_PATH.exists():
        raise ScannerPageError("missing data/scanner_latest.json — run run_scanner.py first")

    template_bytes = TEMPLATE.stat().st_size
    if template_bytes > MAX_TEMPLATE_BYTES:
        raise ScannerPageError(
            f"{TEMPLATE.name} is {template_bytes / 1024:.0f} KB, over the "
            f"{MAX_TEMPLATE_BYTES // 1024} KB source-file limit"
        )

    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    assert_payload_usable(payload)
    history_note = assert_history_covers(payload)

    out = inject(TEMPLATE.read_text(encoding="utf-8"), payload)
    assert_output_clean(out)

    print(f"template {template_bytes / 1024:.0f} KB, "
          f"{payload['n_rows']} rows as of {payload['as_of']}")
    print(f"  {history_note}")
    if args.check:
        print(f"check only — would write {len(out) / 1024:.0f} KB")
        return 0

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(out, encoding="utf-8")
    print(f"wrote {OUT_PATH.relative_to(ROOT)} ({OUT_PATH.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ScannerPageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
