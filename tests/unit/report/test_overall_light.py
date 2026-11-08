from __future__ import annotations

from typing import Any

import pytest

from guard.report import overall_light


def _light(**over: Any) -> str:
    args: dict[str, Any] = {
        "weekly_light": "green",
        "weekly_overdue": False,
        "daily_status": "ok",
        "daily_fast_path_recent": False,
        "snapshot_failed": False,
        "alert_problem": None,
        "paused": False,
    }
    args.update(over)
    return overall_light(**args)


def test_everything_fine_is_green() -> None:
    assert _light() == "green"


def test_an_overdue_week_is_red_even_when_last_week_was_green() -> None:
    assert _light(weekly_light="green", weekly_overdue=True) == "red"


@pytest.mark.parametrize("status", ["failed", "stopped", "locked"])
def test_a_daily_run_that_did_not_complete_is_red(status: str) -> None:
    assert _light(daily_status=status) == "red"


def test_a_recent_fast_path_event_is_red() -> None:
    assert _light(daily_fast_path_recent=True) == "red"


def test_a_red_weekly_light_stays_red() -> None:
    assert _light(weekly_light="red") == "red"


@pytest.mark.parametrize(
    "over",
    [
        {"snapshot_failed": True},
        {"alert_problem": "not configured"},
        {"alert_problem": "the last alert failed on every channel"},
        {"paused": True},
        {"weekly_light": "amber"},
    ],
)
def test_these_are_amber(over: dict[str, Any]) -> None:
    assert _light(**over) == "amber"


def test_red_beats_amber() -> None:
    assert _light(paused=True, weekly_overdue=True, alert_problem="not configured") == "red"


def test_no_weekly_run_yet_and_no_problem_is_green() -> None:
    assert _light(weekly_light=None, daily_status=None) == "green"
