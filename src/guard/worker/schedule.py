"""The scheduler: weekly advice, the daily check and the daily snapshot, all in local time."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from guard.config.models import Schedule
from guard.ingest.snapshot_job import daily_trigger, run_snapshot
from guard.notify.deliver import push_static
from guard.notify.heartbeat import HeartbeatStatus, heartbeat_job
from guard.notify.ping import ping_dead_man
from guard.run.daily import run_daily
from guard.run.deps import Deps, log_notice
from guard.run.redact import redact_amounts
from guard.run.results import DailyResult, Notice, RunResult
from guard.run.weekly import run_weekly
from guard.storage import open_ops, run_store

log = logging.getLogger("guard.worker")

WEEKLY_JOB = "weekly-advice"
DAILY_JOB = "daily-check"
SNAPSHOT_JOB = "daily-snapshot"
HEARTBEAT_JOB = "weekly-heartbeat"
TICK_JOB = "scheduler-tick"
_JOB_KIND = {
    WEEKLY_JOB: "weekly",
    DAILY_JOB: "daily",
    SNAPSHOT_JOB: "snapshot",
    HEARTBEAT_JOB: "heartbeat",
}

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


def heartbeat_trigger(schedule: Schedule) -> CronTrigger:
    """Every day at ``heartbeat_time`` (18:00 by default). Each pass judges the latest week whose
    run is due, so a dead scheduler or a missed week is found within a day, not a week."""
    hour, minute = schedule.heartbeat_time.split(":")
    return CronTrigger(hour=int(hour), minute=int(minute), timezone=ZoneInfo(schedule.timezone))


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
    """What the owner must be told about a finished job: the weekly report, a run that did not
    complete, the events of a daily check, or a missing weekly run. Reasons are already free of
    amounts."""
    if isinstance(outcome, RunResult):
        if outcome.status in ("ok", "paused"):
            return [Notice(kind, "report", outcome.slot, "", result=outcome)]
        if outcome.status in ("stopped", "failed", "locked"):
            return [Notice(kind, outcome.status, outcome.slot, outcome.reason or outcome.status)]
    if isinstance(outcome, DailyResult):
        if outcome.status in ("stopped", "failed", "locked"):
            return [Notice(kind, outcome.status, outcome.slot, outcome.reason or outcome.status)]
        return [
            Notice(kind, "event", outcome.slot, e.message, result=outcome, event=e)
            for e in outcome.events
        ]
    if isinstance(outcome, HeartbeatStatus) and outcome.state == "missing":
        return [Notice(kind, "missing", outcome.slot, "weekly advice is missing")]
    return []


StaticPush = Callable[[str, str, str], object]


def _static_push(title: str, body: str, kind: str) -> object:
    return push_static(title, body, kind=kind)


def guarded(
    name: str,
    kind: str,
    source: DepsSource,
    job: Callable[[Deps, datetime], object],
    *,
    static_push: StaticPush = _static_push,
    ping: Callable[[], None] = ping_dead_man,
) -> Callable[[], None]:
    """A scheduler job that never raises. Finished weekly runs, stopped, failed and locked runs,
    daily events and a missing weekly run go to ``deps.notify`` while the job's deps are still
    open, so they are alerts, not just log lines. If the deps cannot even be opened, a fixed-text
    push goes straight from the secrets. After a good weekly run and after each heartbeat pass the
    dead-man ping fires."""

    def fire() -> None:
        pinged = False
        try:
            with _deps(source) as deps:
                try:
                    outcome = job(deps, deps.clock())
                    log.info("%s finished: %s", name, getattr(outcome, "status", outcome))
                    pending = _notices(kind, outcome)
                    pinged = kind == "heartbeat" or (
                        kind == "weekly"
                        and isinstance(outcome, RunResult)
                        and outcome.status in ("ok", "paused")
                    )
                except Exception as e:  # noqa: BLE001 - the scheduler must keep running
                    log.error("%s failed: %s", name, type(e).__name__)
                    pending = [
                        Notice(
                            kind,
                            "failed",
                            "",
                            f"{name} failed: {redact_amounts(type(e).__name__)}",
                        )
                    ]
                for notice in pending:
                    try:
                        deps.notify(notice)
                    except Exception as e:  # noqa: BLE001 - a broken alert path must not stop jobs
                        log.error(
                            "could not deliver a %s notice: %s", notice.status, type(e).__name__
                        )
        except Exception as e:  # noqa: BLE001 - deps could not be opened
            log.error("%s could not start: %s", name, type(e).__name__)
            log_notice(Notice(kind, "failed", "", f"{name} could not start"))
            _safely(static_push, f"Portfolio Guard: {name} could not start", _COULD_NOT_START, kind)
        if pinged:
            _safely(ping)

    return fire


_COULD_NOT_START = "The job could not open its database or settings. Check the worker log."


def _safely(fn: Callable[..., object], *args: str) -> None:
    try:
        fn(*args)
    except Exception as e:  # noqa: BLE001 - the last-resort path must not raise either
        log.error("fallback step failed: %s", type(e).__name__)


def tick_job(source: DepsSource) -> Callable[[], None]:
    """Write the scheduler's heartbeat (``scheduler_tick``) so a healthcheck can tell a live
    scheduler from a frozen one. It opens only the small operations database."""

    def fire() -> None:
        try:
            now = datetime.now(UTC)
            if isinstance(source, Deps):
                run_store.set_scheduler_tick(source.ops, now)
                return
            conn = open_ops()
            try:
                run_store.set_scheduler_tick(conn, now)
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 - a missing tick is what the healthcheck reports
            log.error("could not record the scheduler tick: %s", type(e).__name__)

    return fire


def missed_job_listener(
    source: DepsSource, *, static_push: StaticPush = _static_push
) -> Callable[[Any], None]:
    """An APScheduler listener: a job that missed its time or raised is announced."""

    def listen(event: Any) -> None:
        job_id = str(getattr(event, "job_id", ""))
        if job_id == TICK_JOB:
            return
        status = "failed" if getattr(event, "exception", None) is not None else "missed"
        kind = _JOB_KIND.get(job_id, "scheduler")
        notice = Notice(kind, status, "", f"{job_id} {status}")
        log.error("scheduler job %s %s", job_id, status)
        try:
            with _deps(source) as deps:
                deps.notify(notice)
        except Exception as e:  # noqa: BLE001 - fall back to fixed text
            log.error("could not announce a %s job: %s", status, type(e).__name__)
            _safely(static_push, f"Portfolio Guard: {kind} job {status}", _COULD_NOT_START, kind)

    return listen


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
    scheduler.add_job(
        guarded("heartbeat", "heartbeat", source, heartbeat_job),
        heartbeat_trigger(schedule),
        id=HEARTBEAT_JOB,
        misfire_grace_time=_until_midnight(schedule.heartbeat_time),
        **options,
    )
    scheduler.add_job(
        tick_job(source),
        IntervalTrigger(minutes=1),
        id=TICK_JOB,
        next_run_time=datetime.now(UTC),
        coalesce=True,
        max_instances=1,
        misfire_grace_time=60,
    )
    scheduler.add_listener(missed_job_listener(source), EVENT_JOB_MISSED | EVENT_JOB_ERROR)
    return scheduler
