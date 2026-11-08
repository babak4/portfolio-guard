"""The status page: pause switch, last successful run of each job, current light."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from guard.config import GuardConfig
from guard.readapi._lights import summary
from guard.readapi.models import LastRun, StatusView
from guard.report import overall_light
from guard.storage import read_store, run_store
from guard.storage.alert_store import alert_problem

KINDS = ("weekly", "daily", "snapshot")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday")
# How long after the scheduled time a weekly run may still be arriving before it counts as late.
OVERDUE_GRACE = timedelta(hours=6)
# A daily run older than this no longer colours the light.
DAILY_RECENT = timedelta(days=3)
_FAILED = ("failed", "stopped", "locked")


def expected_weekly_slot(cfg: GuardConfig, now: datetime) -> date:
    """The slot (a Monday) whose run should already have completed at ``now``."""
    local = now.astimezone(ZoneInfo(cfg.schedule.timezone))
    monday = local.date() - timedelta(days=local.weekday())
    run_day = monday + timedelta(days=_WEEKDAYS.index(cfg.schedule.weekly_weekday))
    due = datetime.combine(
        run_day, time.fromisoformat(cfg.schedule.weekly_time), tzinfo=local.tzinfo
    )
    return monday if local >= due + OVERDUE_GRACE else monday - timedelta(days=7)


def status_view(conn: sqlite3.Connection, cfg: GuardConfig, now: datetime) -> StatusView:
    paused = run_store.get_paused(conn)
    last_runs = []
    for kind in KINDS:
        last = run_store.last_successful_run(conn, kind)
        finished = (
            None
            if last is None or last.finished_at is None
            else datetime.fromisoformat(last.finished_at)
        )
        last_runs.append(
            LastRun(kind, None if last is None else last.slot, finished,
                    None if finished is None else now - finished)
        )  # fmt: skip
    weekly_rows = read_store.recent_runs(conn, limit=1, kind="weekly")
    latest = summary(weekly_rows[0]) if weekly_rows else None
    has_runs = bool(read_store.recent_runs(conn, limit=1))

    expected = expected_weekly_slot(cfg, now).isoformat()
    state = run_store.weekly_slot_state(conn, expected)
    last_weekly = next(r for r in last_runs if r.kind == "weekly")
    overdue = has_runs and state == "missing"
    warning = None
    if overdue:
        warning = (
            f"The weekly run for the week of {expected} has not completed. "
            f"Last successful weekly run: {last_weekly.slot or 'never'}."
        )

    daily_rows = read_store.recent_runs(conn, limit=1, kind="daily")
    daily = daily_rows[0] if daily_rows else None
    daily_recent = (
        daily is not None and date.fromisoformat(daily.slot) >= (now - DAILY_RECENT).date()
    )
    snapshot_rows = read_store.recent_runs(conn, limit=1, kind="snapshot")
    snapshot_failed = bool(snapshot_rows) and snapshot_rows[0].status in _FAILED
    problem = alert_problem(conn)
    computed = overall_light(
        None if latest is None else latest.light,
        overdue,
        daily.status if daily is not None and daily_recent else None,
        daily is not None and daily_recent and daily.fast_path,
        snapshot_failed,
        problem,
        paused or state == "paused",
    )
    light = None if not has_runs and computed == "green" else computed
    return StatusView(
        paused=paused,
        has_runs=has_runs,
        light=light,
        latest_weekly=latest,
        last_runs=tuple(last_runs),
        weekly_overdue=overdue,
        expected_weekly_slot=expected,
        warning=warning,
        alert_deliveries=tuple(read_store.recent_alert_deliveries(conn, 5)),
        alert_problem=problem,
        week_paused=state == "paused",
        snapshot_failed=snapshot_failed,
    )
