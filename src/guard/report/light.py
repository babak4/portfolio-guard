"""The red/amber/green status light. One rule, used by alerts and the dashboard alike.

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
