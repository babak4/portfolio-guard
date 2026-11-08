from __future__ import annotations

import pytest

from guard.report import daily_light, weekly_light


@pytest.mark.parametrize("status", ["stopped", "failed", "locked"])
def test_a_run_that_did_not_complete_is_red(status: str) -> None:
    assert weekly_light(status, None, None, warn_flags=0, trades=0) == "red"


def test_blocked_advice_is_red() -> None:
    assert weekly_light("ok", "hold", "blocked", warn_flags=0, trades=0) == "red"


def test_nothing_to_do_is_green() -> None:
    assert weekly_light("ok", "hold", "advice", warn_flags=0, trades=0) == "green"


@pytest.mark.parametrize("kind", ["derisk", "continue", "reentry"])
def test_a_ladder_move_is_amber(kind: str) -> None:
    assert weekly_light("ok", kind, "advice", warn_flags=0, trades=0) == "amber"


def test_a_warning_or_a_trade_to_make_is_amber() -> None:
    assert weekly_light("ok", "hold", "advice", warn_flags=1, trades=0) == "amber"
    assert weekly_light("ok", "hold", "advice", warn_flags=0, trades=2) == "amber"


def test_paused_is_amber() -> None:
    assert weekly_light("paused", None, None, warn_flags=0, trades=0) == "amber"


def test_a_data_gap_decision_is_amber() -> None:
    assert weekly_light("ok", "data_gap", "advice", warn_flags=0, trades=0) == "amber"


def test_red_beats_amber() -> None:
    assert weekly_light("stopped", "derisk", "advice", warn_flags=3, trades=4) == "red"


def test_daily_light() -> None:
    assert daily_light(event_count=0, fast_path=False) == "green"
    assert daily_light(event_count=2, fast_path=False) == "amber"
    assert daily_light(event_count=1, fast_path=True) == "red"
