"""Silence is never "all clear": alert problems, overdue weeks and pauses reach the status view."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from guard import readapi
from guard.config.models import GuardConfig
from guard.storage import open_ops, run_store
from guard.storage.alert_store import (
    PROBLEM_FAILED_EVERYWHERE,
    PROBLEM_NOT_CONFIGURED,
    AlertStore,
)

from .conftest import add_run

AFTER = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
LATE = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)  # the week of 2026-11-16 is a day overdue


def _alerts(configured: bool | None, *, failed: bool = False) -> None:
    c = open_ops()
    try:
        if configured is not None:
            run_store.set_control(
                c, run_store.ALERTS_CONFIGURED_KEY, "1" if configured else "0", AFTER
            )
        if failed:
            AlertStore(c).record(
                kind="weekly", urgency="info", channel="telegram", ok=False, error="x", now=AFTER
            )
    finally:
        c.close()


def test_not_configured_is_a_problem_and_forces_at_least_amber(
    conn: sqlite3.Connection, cfg: GuardConfig
) -> None:
    _alerts(False)
    s = readapi.status_view(conn, cfg, AFTER)
    assert s.alert_problem == PROBLEM_NOT_CONFIGURED
    assert s.light == "amber"  # the weekly run alone would be green


def test_a_failed_last_delivery_is_a_problem(conn: sqlite3.Connection, cfg: GuardConfig) -> None:
    _alerts(True, failed=True)
    s = readapi.status_view(conn, cfg, AFTER)
    assert s.alert_problem == PROBLEM_FAILED_EVERYWHERE and s.light != "green"


def test_a_good_delivery_after_a_failure_clears_the_problem(
    conn: sqlite3.Connection, cfg: GuardConfig
) -> None:
    _alerts(True, failed=True)
    c = open_ops()
    AlertStore(c).record(
        kind="test", urgency="info", channel="ntfy", ok=True, error=None, now=AFTER
    )
    c.close()
    assert readapi.status_view(conn, cfg, AFTER).alert_problem is None


def test_a_fresh_install_with_no_alert_history_has_no_problem(
    empty_conn: sqlite3.Connection, cfg: GuardConfig
) -> None:
    assert readapi.status_view(empty_conn, cfg, AFTER).alert_problem is None


def test_an_overdue_week_is_red_even_though_last_week_was_green(
    conn: sqlite3.Connection, cfg: GuardConfig
) -> None:
    s = readapi.status_view(conn, cfg, LATE)
    assert s.weekly_overdue and s.light == "red"


def test_a_paused_week_is_paused_not_overdue(conn: sqlite3.Connection, cfg: GuardConfig) -> None:
    add_run("paused", "2026-11-16")
    s = readapi.status_view(conn, cfg, LATE)
    assert not s.weekly_overdue and s.week_paused
    assert s.warning is None and s.light == "amber"


def test_a_stopped_week_is_still_overdue(conn: sqlite3.Connection, cfg: GuardConfig) -> None:
    add_run("stopped", "2026-11-16", reason="why")
    assert readapi.status_view(conn, cfg, LATE).weekly_overdue


def test_a_recent_failed_daily_run_makes_it_red_but_an_old_one_does_not(
    conn: sqlite3.Connection, cfg: GuardConfig
) -> None:
    add_run("failed", "2026-11-11", kind="daily")
    assert readapi.status_view(conn, cfg, AFTER).light == "red"
    assert readapi.status_view(conn, cfg, datetime(2026, 11, 16, 9, 0, tzinfo=UTC)).light != "red"


def test_a_failed_snapshot_is_amber(conn: sqlite3.Connection, cfg: GuardConfig) -> None:
    add_run("ok", "2026-11-11", kind="snapshot")
    add_run("failed", "2026-11-12", kind="snapshot")
    # the sample weekly run is already amber (trades); make the comparison on the flag itself
    assert readapi.status_view(conn, cfg, AFTER).snapshot_failed
