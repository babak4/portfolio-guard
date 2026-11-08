"""The red/amber/green status light. ``weekly_light`` and ``daily_light`` are the rules the alert
messages and the dashboard both call; ``overall_light`` combines them with the state of the
system (overdue week, failed jobs, broken alerts, pause) for the dashboard's headline light.

Green: nothing to do. Amber: look at it (a ladder move, a warning, trades to make, paused, or an
unusual market day). Red: the run did not complete, the advice was blocked, or the fast path
applies.
"""

from __future__ import annotations

from typing import Literal

Light = Literal["green", "amber", "red"]

_FAILED_RUNS = frozenset({"stopped", "failed", "locked"})
_LADDER_MOVES = frozenset({"derisk", "continue", "reentry", "data_gap"})


def weekly_light(
    run_status: str,
    ladder_kind: str | None,
    advice_status: str | None,
    *,
    warn_flags: int,
    trades: int,
) -> Light:
    if run_status in _FAILED_RUNS or advice_status == "blocked":
        return "red"
    if run_status == "paused" or ladder_kind in _LADDER_MOVES or warn_flags > 0 or trades > 0:
        return "amber"
    return "green"


def daily_light(*, event_count: int, fast_path: bool) -> Light:
    if fast_path:
        return "red"
    return "amber" if event_count > 0 else "green"


def overall_light(
    weekly_light: Light | None,
    weekly_overdue: bool,
    daily_status: str | None,
    daily_fast_path_recent: bool,
    snapshot_failed: bool,
    alert_problem: str | None,
    paused: bool,
) -> Light:
    """The headline light. Red when the week's advice is overdue, the latest weekly run is red,
    the latest recent daily run did not complete or hit the fast path. Amber when the snapshot
    job failed, alerts cannot reach the owner, advice is paused or the weekly run is amber.
    Silence is never green: a missing week is red whatever last week looked like."""
    if (
        weekly_overdue
        or weekly_light == "red"
        or daily_status in _FAILED_RUNS
        or daily_fast_path_recent
    ):
        return "red"
    if snapshot_failed or alert_problem is not None or paused or weekly_light == "amber":
        return "amber"
    return "green"
