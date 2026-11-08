"""The status light of a stored run, by the shared rules in ``guard.report``."""

from __future__ import annotations

from guard.readapi.models import RunSummary
from guard.report import Light, daily_light, weekly_light
from guard.storage.read_store import RunRow

_FAILED = frozenset({"stopped", "failed", "locked"})


def light_for(r: RunRow) -> Light | None:
    if r.kind == "weekly":
        return weekly_light(
            r.status, r.ladder_kind, r.advice_status, warn_flags=r.warn_flags, trades=r.trades
        )
    if r.status in _FAILED:
        return "red"
    if r.kind == "daily":
        return daily_light(event_count=r.event_count, fast_path=r.fast_path)
    return "green"


def summary(r: RunRow) -> RunSummary:
    return RunSummary(
        run_id=r.run_id,
        kind=r.kind,
        slot=r.slot,
        status=r.status,
        reason=r.stop_reason,
        started_at=r.started_at,
        finished_at=r.finished_at,
        light=light_for(r),
        ladder_kind=r.ladder_kind,
        trades=r.trades,
        warn_flags=r.warn_flags,
    )
