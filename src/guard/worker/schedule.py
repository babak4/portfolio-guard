"""The scheduler: weekly advice, the daily check and the daily snapshot, all in local time."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from guard.config.models import Schedule
from guard.ingest.snapshot_job import daily_trigger, run_snapshot
from guard.run.daily import run_daily
from guard.run.deps import Deps, log_notice
from guard.run.redact import redact_amounts
from guard.run.results import DailyResult, Notice, RunResult
from guard.run.weekly import run_weekly

log = logging.getLogger("guard.worker")

WEEKLY_JOB = "weekly-advice"
DAILY_JOB = "daily-check"
SNAPSHOT_JOB = "daily-snapshot"

# A job that missed its time (the host was off) still runs if it is late by less than this: the
# same day, so the daily jobs answer the day they were meant for.
_DAY = 24 * 3600

DepsSource = Deps | Callable[[], Deps]


def weekly_trigger(schedule: Schedule) -> CronTrigger:
    hour, minute = schedule.weekly_time.split(":")
    return CronTrigger(
        day_of_week=schedule.weekly_weekday[:3],
        hour=int(hour),
        minute=int(minute),
        timezone=ZoneInfo(schedule.timezone),
    )


def _until_midnight(time_text: str) -> int:
    hour, minute = time_text.split(":")
    return _DAY - (int(hour) * 3600 + int(minute) * 60)


@contextmanager
def _deps(source: DepsSource) -> Any:
    """A ``Deps`` for one job. A factory's deps are opened for the job and closed after it, so
    no database connection is shared between scheduler threads."""
    if isinstance(source, Deps):
        yield source
        return
    deps = source()
    try:
        yield deps
    finally:
        deps.close()


def _notices(kind: str, outcome: object) -> list[Notice]:
    """What the owner must be told about a finished job: a run that did not complete, or the
    events of a daily check. Reasons are already free of amounts."""
    if isinstance(outcome, RunResult) and outcome.status in ("stopped", "failed", "locked"):
        return [Notice(kind, outcome.status, outcome.slot, outcome.reason or outcome.status)]
    if isinstance(outcome, DailyResult):
        if outcome.status in ("stopped", "failed", "locked"):
            return [Notice(kind, outcome.status, outcome.slot, outcome.reason or outcome.status)]
        return [Notice(kind, "event", outcome.slot, e.message) for e in outcome.events]
    return []


def guarded(
    name: str, kind: str, source: DepsSource, job: Callable[[Deps, datetime], object]
) -> Callable[[], None]:
    """A scheduler job that never raises. Stopped, failed and locked runs and daily events go to
    ``deps.notify`` so they are alerts, not just log lines."""

    def fire() -> None:
        notify = log_notice
        try:
            with _deps(source) as deps:
                notify = deps.notify
                outcome = job(deps, datetime.now(UTC))
            log.info("%s finished: %s", name, getattr(outcome, "status", outcome))
            pending = _notices(kind, outcome)
        except Exception as e:  # noqa: BLE001 - the scheduler must keep running
            log.error("%s failed: %s", name, type(e).__name__)
            pending = [
                Notice(kind, "failed", "", f"{name} failed: {redact_amounts(type(e).__name__)}")
            ]
        for notice in pending:
            try:
                notify(notice)
            except Exception as e:  # noqa: BLE001 - a broken alert path must not stop the jobs
                log.error("could not deliver a %s notice: %s", notice.status, type(e).__name__)

    return fire


def _snapshot(deps: Deps, now: datetime) -> str:
    return run_snapshot(
        now, config=deps.config, client_for=deps.client_for, manual_path=deps.manual_path
    )


def build_scheduler(source: DepsSource, schedule: Schedule | None = None) -> BlockingScheduler:
    """Weekly advice, daily check and daily snapshot on a cron in ``schedule.timezone``.

    ``source`` is a ``Deps`` or a function that makes one (preferred when running for real).
    """
    if schedule is None:
        with _deps(source) as deps:
            schedule = deps.config.schedule
    scheduler = BlockingScheduler(timezone=ZoneInfo(schedule.timezone))
    options: dict[str, Any] = {"coalesce": True, "max_instances": 1}
    scheduler.add_job(
        guarded("weekly run", "weekly", source, run_weekly),
        weekly_trigger(schedule),
        id=WEEKLY_JOB,
        misfire_grace_time=_until_midnight(schedule.weekly_time),
        **options,
    )
    scheduler.add_job(
        guarded("daily check", "daily", source, run_daily),
        daily_trigger(schedule),
        id=DAILY_JOB,
        misfire_grace_time=_until_midnight(schedule.daily_check_time),
        **options,
    )
    scheduler.add_job(
        guarded("snapshot", "snapshot", source, _snapshot),
        daily_trigger(schedule),
        id=SNAPSHOT_JOB,
        misfire_grace_time=_until_midnight(schedule.daily_check_time),
        **options,
    )
    return scheduler
