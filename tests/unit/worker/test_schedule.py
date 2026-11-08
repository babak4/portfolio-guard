from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger

from guard.config import load_config
from guard.run.results import DailyEvent, DailyResult, Notice, RunResult
from guard.worker.schedule import (
    DAILY_JOB,
    HEARTBEAT_JOB,
    SNAPSHOT_JOB,
    TICK_JOB,
    WEEKLY_JOB,
    build_scheduler,
    guarded,
)
from tests.unit.run.fakes import make_deps, market

LONDON = ZoneInfo("Europe/London")


@pytest.fixture
def scheduler(data_dir: Path):  # type: ignore[no-untyped-def]
    prices, macro = market(datetime(2026, 11, 10).date())
    deps = make_deps(prices, macro)
    yield build_scheduler(deps)
    deps.close()


def _fire_times(trigger: CronTrigger, since: datetime, count: int) -> list[datetime]:
    out: list[datetime] = []
    previous = None
    now = since
    for _ in range(count):
        nxt = trigger.get_next_fire_time(previous, now)
        assert nxt is not None
        out.append(nxt)
        previous, now = nxt, nxt + timedelta(seconds=1)
    return out


def test_weekly_job_fires_wednesday_noon_london_across_the_autumn_clock_change(scheduler) -> None:  # type: ignore[no-untyped-def]
    job = scheduler.get_job(WEEKLY_JOB) or next(
        j for j in scheduler.get_jobs() if j.id == WEEKLY_JOB
    )
    times = _fire_times(job.trigger, datetime(2026, 10, 15, tzinfo=LONDON), 3)
    assert [t.date().isoformat() for t in times] == ["2026-10-21", "2026-10-28", "2026-11-04"]
    assert all(t.weekday() == 2 and (t.hour, t.minute) == (12, 0) for t in times)
    # 21 October is summer time (UTC+1); 28 October, after the clocks went back, is UTC+0
    assert [t.utcoffset() for t in times] == [timedelta(hours=1), timedelta(0), timedelta(0)]


def test_weekly_job_keeps_local_noon_across_the_spring_clock_change(scheduler) -> None:  # type: ignore[no-untyped-def]
    job = next(j for j in scheduler.get_jobs() if j.id == WEEKLY_JOB)
    times = _fire_times(job.trigger, datetime(2026, 3, 22, tzinfo=LONDON), 2)
    assert [t.date().isoformat() for t in times] == ["2026-03-25", "2026-04-01"]
    assert all((t.hour, t.minute) == (12, 0) for t in times)
    assert [t.utcoffset() for t in times] == [timedelta(0), timedelta(hours=1)]


def test_four_jobs_with_misfire_coalesce_and_single_instance(scheduler) -> None:  # type: ignore[no-untyped-def]
    jobs = {j.id: j for j in scheduler.get_jobs()}
    assert set(jobs) == {WEEKLY_JOB, DAILY_JOB, SNAPSHOT_JOB, HEARTBEAT_JOB, TICK_JOB}
    for job in (j for j in jobs.values() if j.id != TICK_JOB):
        assert job.coalesce is True and job.max_instances == 1
        assert job.misfire_grace_time is not None and job.misfire_grace_time >= 3600
    # a late start must still be in the same ISO week; the grace stays within the day
    assert jobs[WEEKLY_JOB].misfire_grace_time == 12 * 3600
    assert jobs[DAILY_JOB].misfire_grace_time == int(1.5 * 3600)
    daily = _fire_times(jobs[DAILY_JOB].trigger, datetime(2026, 11, 9, 23, tzinfo=LONDON), 1)[0]
    assert (daily.date().isoformat(), daily.hour, daily.minute) == ("2026-11-10", 22, 30)


def test_scheduler_uses_the_configured_timezone(scheduler) -> None:  # type: ignore[no-untyped-def]
    assert str(scheduler.timezone) == "Europe/London"
    assert load_config()[0].schedule.weekly_weekday == "wednesday"


def _fire(data_dir: Path, kind: str, job, notices: list[Notice]) -> None:  # type: ignore[no-untyped-def]
    prices, macro = market(datetime(2026, 11, 10).date())
    deps = make_deps(prices, macro)
    deps.notify = notices.append
    guarded(f"{kind} job", kind, deps, job)()
    deps.close()


@pytest.mark.parametrize("status", ["stopped", "locked"])
def test_a_stopped_or_locked_run_is_announced(data_dir: Path, status: str) -> None:
    got: list[Notice] = []
    _fire(
        data_dir,
        "weekly",
        lambda d, n: RunResult(status, "weekly", "2026-11-09", reason="why"),
        got,
    )
    assert got == [Notice("weekly", status, "2026-11-09", "why")]


def test_a_failed_job_is_announced_without_its_message(data_dir: Path) -> None:
    got: list[Notice] = []

    def boom(deps, now):  # type: ignore[no-untyped-def]
        raise RuntimeError("balance 12345.67")

    _fire(data_dir, "snapshot", boom, got)
    assert len(got) == 1 and got[0].kind == "snapshot" and got[0].status == "failed"
    assert "RuntimeError" in got[0].message and "12345" not in got[0].message


def test_each_daily_event_is_announced_and_a_quiet_day_is_not(data_dir: Path) -> None:
    events = (
        DailyEvent("day_drop", "The market fell 5.1% in a day.", False),
        DailyEvent("volatility", "Volatility is 30%.", False),
    )
    got: list[Notice] = []
    _fire(data_dir, "daily", lambda d, n: DailyResult("ok", "2026-11-10", events=events), got)
    assert [(n.kind, n.status, n.message) for n in got] == [
        ("daily", "event", "The market fell 5.1% in a day."),
        ("daily", "event", "Volatility is 30%."),
    ]
    quiet: list[Notice] = []
    _fire(data_dir, "daily", lambda d, n: DailyResult("ok", "2026-11-10"), quiet)
    assert quiet == []


@pytest.mark.parametrize("status", ["ok", "paused"])
def test_a_finished_weekly_run_sends_the_report(data_dir: Path, status: str) -> None:
    got: list[Notice] = []
    result = RunResult(status, "weekly", "2026-11-09")
    _fire(data_dir, "weekly", lambda d, n: result, got)
    assert [(n.kind, n.status, n.slot) for n in got] == [("weekly", "report", "2026-11-09")]
    assert got[0].result == result


def test_the_heartbeat_job_runs_every_evening_london(scheduler) -> None:  # type: ignore[no-untyped-def]
    job = next(j for j in scheduler.get_jobs() if j.id == HEARTBEAT_JOB)
    times = _fire_times(job.trigger, datetime(2026, 10, 15, tzinfo=LONDON), 3)
    assert all((t.hour, t.minute) == (18, 0) for t in times)
    assert [t.date().isoformat() for t in times] == ["2026-10-15", "2026-10-16", "2026-10-17"]
    assert job.coalesce is True and job.max_instances == 1


def test_a_notifier_that_raises_does_not_stop_the_scheduler(data_dir: Path) -> None:
    prices, macro = market(datetime(2026, 11, 10).date())
    deps = make_deps(prices, macro)

    def broken(_notice: Notice) -> None:
        raise OSError("push service down")

    deps.notify = broken
    guarded("weekly job", "weekly", deps, lambda d, n: RunResult("stopped", "weekly", "s"))()
    deps.close()
