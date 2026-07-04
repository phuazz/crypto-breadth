"""
tests/conftest.py
-----------------
Shared setup for the engine unit-test suite. Puts scripts/ on sys.path so the
tests can `from backtest import ...` the production engine directly.

These tests run on SYNTHETIC in-memory panels only — no network, no real
prices.parquet, no dashboard build. They pin the engine's structural
invariants (no look-ahead, gate tiering, point-in-time investability, cost
application, ranking, UTC date boundaries, wealth-ratio CAGR) so a refactor
that quietly changes semantics is caught. The end-to-end regression backstop on
the real parquet stays in scripts/test_backtest.py (wired into daily-check.yml).
"""
from __future__ import annotations

import sys
from pathlib import Path

# scripts/ holds backtest.py — make it importable from tests/.
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
