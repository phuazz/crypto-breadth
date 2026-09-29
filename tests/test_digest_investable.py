"""
Digest deviation chart / on-the-cusp set (scripts/notify.py:investable_ma_distances).

A coin whose series stops early (LUNA ends 2022-05-13, flagged investable on its
own last row) must not enter the "investable today" set. The 2026-09-29 digest
drew LUNA at -100% and titled the chart "12 of 14" against an engine breadth of
12 of 13.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import notify


def _coin(last_date, close, ma, investable=True, frozen=False):
    return {"dates": ["2020-01-01", last_date], "close": [1.0, close], "ma": [1.0, ma],
            "investable": [True, investable], "frozen": frozen}


SIGNALS = {
    "BTC": _coin("2026-09-28", 110.0, 100.0),
    "TRX": _coin("2026-09-28", 99.7, 100.0),
    "FIL": _coin("2026-09-28", 50.0, 100.0, investable=False),
    "LUNA": _coin("2022-05-13", 0.00005, 83.5),                  # dead, stale, last row investable
    "EOS": _coin("2026-07-04", 0.0575, 0.066, investable=False, frozen=True),
}


def test_monitor_names_are_authoritative():
    rows = dict(notify.investable_ma_distances(SIGNALS, ["BTC", "TRX"]))
    assert set(rows) == {"BTC", "TRX"}
    assert abs(rows["BTC"] - 0.10) < 1e-12


def test_fallback_drops_stale_and_frozen_series():
    rows = dict(notify.investable_ma_distances(SIGNALS))
    assert set(rows) == {"BTC", "TRX"}


def test_empty_input():
    assert notify.investable_ma_distances({}) == []
    assert notify.investable_ma_distances(None, ["BTC"]) == []
