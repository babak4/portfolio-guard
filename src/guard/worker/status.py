"""The heartbeat: whether advice is paused and when each job last succeeded."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from guard.storage import run_store

KINDS = ("weekly", "daily", "snapshot")


@dataclass(frozen=True)
class LastRun:
    kind: str
    slot: str | None  # None when the job has never succeeded
    age: timedelta | None


@dataclass(frozen=True)
class StatusReport:
    paused: bool
    last_runs: tuple[LastRun, ...]


def _age(finished_at: str | None, now: datetime) -> timedelta | None:
    if finished_at is None:
        return None
    return now - datetime.fromisoformat(finished_at)


def build_status(conn: sqlite3.Connection, now: datetime) -> StatusReport:
    runs = []
    for kind in KINDS:
        last = run_store.last_successful_run(conn, kind)
        runs.append(
            LastRun(kind, None, None)
            if last is None
            else LastRun(kind, last.slot, _age(last.finished_at, now))
        )
    return StatusReport(run_store.get_paused(conn), tuple(runs))


def format_age(age: timedelta | None) -> str:
    if age is None:
        return "unknown age"
    seconds = max(0, int(age.total_seconds()))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h ago"
    if hours:
        return f"{hours}h {minutes}m ago"
    return f"{minutes}m ago"


def format_status(report: StatusReport) -> str:
    lines = [f"paused: {'yes' if report.paused else 'no'}"]
    for run in report.last_runs:
        if run.slot is None:
            lines.append(f"{run.kind}: never succeeded")
        else:
            lines.append(f"{run.kind}: last ok run {run.slot} ({format_age(run.age)})")
    return "\n".join(lines)
