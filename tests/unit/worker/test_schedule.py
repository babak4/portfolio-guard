from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger

from guard.config import load_config
from guard.worker.schedule import DAILY_JOB, SNAPSHOT_JOB, WEEKLY_JOB, build_scheduler
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


def test_three_jobs_with_misfire_coalesce_and_single_instance(scheduler) -> None:  # type: ignore[no-untyped-def]
    jobs = {j.id: j for j in scheduler.get_jobs()}
    assert set(jobs) == {WEEKLY_JOB, DAILY_JOB, SNAPSHOT_JOB}
    for job in jobs.values():
        assert job.coalesce is True and job.max_instances == 1
        assert job.misfire_grace_time is not None and job.misfire_grace_time >= 3600
    # the weekly slot is the local date, so a late start must still be before midnight
    assert jobs[WEEKLY_JOB].misfire_grace_time == 12 * 3600
    assert jobs[DAILY_JOB].misfire_grace_time == int(1.5 * 3600)
    daily = _fire_times(jobs[DAILY_JOB].trigger, datetime(2026, 11, 9, 23, tzinfo=LONDON), 1)[0]
    assert (daily.date().isoformat(), daily.hour, daily.minute) == ("2026-11-10", 22, 30)


def test_scheduler_uses_the_configured_timezone(scheduler) -> None:  # type: ignore[no-untyped-def]
    assert str(scheduler.timezone) == "Europe/London"
    assert load_config()[0].schedule.weekly_weekday == "wednesday"
