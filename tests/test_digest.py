"""
Digest scheduling contract for the email notifier (scripts/notify.py).

_digest_due decides whether the regular status digest fires on a given run. Two
properties matter and have both bitten in practice:

  1. De-dup — a scheduled cadence must not send twice for the same period
     (guarded by state['last_digest_date']).
  2. Force override — the manual "send digest now" button (DIGEST_FORCE) must
     deliver even if a scheduled digest already went out today, and must NEVER
     fire on the unattended cron path (empty DIGEST_FORCE).

Pure logic: env + a small state dict in, (due, label, window) out. No network,
no parquet — weekdays come from the real UTC clock, never from memory, so the
weekday-gate cases derive their expectation from datetime rather than asserting
a hard-coded day.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import notify

_ENV_KEYS = ("DIGEST_FORCE", "DIGEST_CADENCE", "DIGEST_WEEKDAY")


def _due(monkeypatch, env: dict, state: dict):
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return notify._digest_due(state)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def test_force_overrides_same_day_dedup(monkeypatch):
    """The exact bug: a digest already went out today, force must still send."""
    due, label, _ = _due(
        monkeypatch,
        {"DIGEST_CADENCE": "weekly", "DIGEST_WEEKDAY": "1", "DIGEST_FORCE": "1"},
        {"last_digest_date": _today()},
    )
    assert due is True


def test_no_force_respects_daily_dedup(monkeypatch):
    """Unattended daily cron: already sent today → must not send again."""
    due, _, _ = _due(monkeypatch, {"DIGEST_CADENCE": "daily"}, {"last_digest_date": _today()})
    assert due is False


def test_no_force_daily_due_when_stale(monkeypatch):
    """Daily cron: last sent well in the past → due."""
    old = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()
    due, _, win = _due(monkeypatch, {"DIGEST_CADENCE": "daily"}, {"last_digest_date": old})
    assert due is True and win == 1


def test_weekly_dedup_holds_regardless_of_weekday(monkeypatch):
    """Weekly, no force, already sent today → not due whatever today's weekday is
    (last == today can never satisfy the gate)."""
    due, _, _ = _due(
        monkeypatch,
        {"DIGEST_CADENCE": "weekly", "DIGEST_WEEKDAY": "1"},
        {"last_digest_date": _today()},
    )
    assert due is False


def test_weekly_fires_only_on_configured_weekday(monkeypatch):
    """Derive expectation from the real clock: matching weekday → due; the next
    weekday → not due. No hard-coded day, so it is stable on any run date."""
    wd = datetime.now(timezone.utc).weekday()
    due_match, _, _ = _due(
        monkeypatch, {"DIGEST_CADENCE": "weekly", "DIGEST_WEEKDAY": str(wd)}, {}
    )
    due_off, _, _ = _due(
        monkeypatch, {"DIGEST_CADENCE": "weekly", "DIGEST_WEEKDAY": str((wd + 1) % 7)}, {}
    )
    assert due_match is True and due_off is False


def test_force_sends_even_when_cadence_off(monkeypatch):
    """A user who set cadence=off but clicks send-now still gets one."""
    due_force, label, _ = _due(
        monkeypatch, {"DIGEST_CADENCE": "off", "DIGEST_FORCE": "1"}, {"last_digest_date": _today()}
    )
    due_plain, _, _ = _due(monkeypatch, {"DIGEST_CADENCE": "off"}, {})
    assert due_force is True and label == "Status"
    assert due_plain is False


def test_force_flag_parsing(monkeypatch):
    """DIGEST_FORCE is truthy only for explicit affirmatives; empty/0/false is
    the cron path and must not force."""
    for falsy in ("", "0", "false", "no", "off"):
        due, _, _ = _due(
            monkeypatch,
            {"DIGEST_CADENCE": "daily", "DIGEST_FORCE": falsy},
            {"last_digest_date": _today()},
        )
        assert due is False, f"{falsy!r} must not force"
    for truthy in ("1", "true", "YES", "On"):
        due, _, _ = _due(
            monkeypatch,
            {"DIGEST_CADENCE": "daily", "DIGEST_FORCE": truthy},
            {"last_digest_date": _today()},
        )
        assert due is True, f"{truthy!r} must force"
