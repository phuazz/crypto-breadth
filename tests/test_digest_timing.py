"""
Digest rebalance-timing wording (scripts/notify.py:rebalance_timing).

The engine sets target weights on Monday's close and executes them at the NEXT
bar (lag_days=1). The digest must say WHEN a gate reading actually trades, and
the answer depends entirely on which weekday `as_of` falls on:

  - as_of IS Monday  -> that reading is the LIVE signal, executing at the next
    day's close. This is the NORMAL scheduled case (DIGEST_WEEKDAY=1, the Tuesday
    00:45 UTC cron reports Monday's close), so it trades the very day the mail is
    read.
  - as_of is any other day -> not actionable; the gate is re-read at the next
    Monday close and trades the day after.

The bug this pins: the note hardcoded "applies at the next Monday rebalance,
executed Tuesday", which was calibrated to an off-schedule Wednesday send and was
therefore WRONG on the routine Tuesday send — telling the reader a trade was six
days away when it executed that night.

All dates here were verified with a date library (see the boundary cases), never
computed from memory. Python: Monday=0 .. Sunday=6; months are 1-indexed.
"""
from __future__ import annotations

from datetime import date, timedelta

import notify
from backtest import Params


def test_constant_mirrors_the_engine():
    """notify.REBALANCE_WEEKDAY duplicates backtest.Params.rebalance_weekday to
    avoid importing the engine. If the engine ever moves off Monday, this fails
    rather than letting the digest silently narrate the wrong day."""
    assert notify.REBALANCE_WEEKDAY == Params().rebalance_weekday


def test_monday_as_of_is_the_live_signal():
    """2026-07-13 is a Monday: the reading IS the signal and trades 07-14."""
    assert date(2026, 7, 13).weekday() == 0  # library-verified, not from memory
    live, phrase, _ex = notify.rebalance_timing("2026-07-13")
    assert live is True
    assert "live signal" in phrase
    assert "2026-07-14" in phrase
    assert _ex == date(2026, 7, 14)  # the exec date drives the subject line


def test_non_monday_as_of_is_not_actionable():
    """2026-07-14 is a Tuesday: the Monday signal already executed today, so this
    reading waits for the 07-20 close and trades 07-21. This is the real case
    from the digest that prompted the fix."""
    assert date(2026, 7, 14).weekday() == 1
    live, phrase, _ex = notify.rebalance_timing("2026-07-14")
    assert live is False
    assert "not yet actionable" in phrase
    assert "2026-07-20" in phrase  # next Monday close = the signal
    assert "2026-07-21" in phrase  # executes the day after
    assert _ex == date(2026, 7, 21)


def test_sunday_as_of_points_at_tomorrow_not_a_week_out():
    """Sunday 2026-07-19 -> the next Monday is the very next day (07-20), not
    seven days later. Guards the `% 7 or 7` offset against an off-by-a-week."""
    assert date(2026, 7, 19).weekday() == 6
    live, phrase, _ex = notify.rebalance_timing("2026-07-19")
    assert live is False
    assert "2026-07-20" in phrase and "2026-07-21" in phrase


# ---- edge cases: month and year boundaries (vault rule) --------------------

def test_month_boundary_execution_rolls_into_next_month():
    """Monday 2026-08-31 executes at the 2026-09-01 close — the offset must cross
    the month end, which naive day arithmetic gets wrong."""
    d = date(2026, 8, 31)
    assert d.weekday() == 0 and (d + timedelta(days=1)).month == 9
    live, phrase, _ex = notify.rebalance_timing("2026-08-31")
    assert live is True
    assert "2026-09-01" in phrase


def test_year_boundary_execution_rolls_into_next_year():
    """Monday 2029-12-31 executes at the 2030-01-01 close — crosses the year."""
    d = date(2029, 12, 31)
    assert d.weekday() == 0 and (d + timedelta(days=1)).year == 2030
    live, phrase, _ex = notify.rebalance_timing("2029-12-31")
    assert live is True
    assert "2030-01-01" in phrase


def test_year_boundary_on_the_not_actionable_branch():
    """Tuesday 2026-12-29 -> next Monday is 2027-01-04, trading 2027-01-05. Both
    the signal and its execution land in the following year."""
    assert date(2026, 12, 29).weekday() == 1
    live, phrase, _ex = notify.rebalance_timing("2026-12-29")
    assert live is False
    assert "2027-01-04" in phrase and "2027-01-05" in phrase
