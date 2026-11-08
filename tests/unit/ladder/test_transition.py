from __future__ import annotations

import pytest

from guard.config.models import Ladder, LadderStep
from guard.ladder import LadderState, Signals, advance, initial_state
from guard.signals import SignalResult

NEG = SignalResult(-0.2, True)
POS = SignalResult(0.1, False)
GAP = SignalResult(None, False, data_gap=True)


def cfg(**kw: object) -> Ladder:
    base: dict[str, object] = {
        "steps": (
            LadderStep(equity=0.90, safe=0.10),
            LadderStep(equity=0.75, safe=0.25),
            LadderStep(equity=0.60, safe=0.40),
            LadderStep(equity=0.50, safe=0.50),
        ),
        "confirmation_runs": 2,
        "fast_path_weekly_drop": 0.10,
        "min_shift": 0.05,
        "cooldown_weeks": 2,
        "max_weekly_change": 0.15,
        "equity_floor": 0.50,
        "reentry_tranches": 4,
    }
    base.update(kw)
    return Ladder.model_validate(base)


def sig(
    trend: bool = False, dd: bool = False, vol: bool = False, ret: float | None = None
) -> Signals:
    return Signals(NEG if trend else POS, NEG if dd else POS, NEG if vol else POS, ret)


def run(state: LadderState, weeks: list[tuple[int, Signals]], c: Ladder) -> list[LadderState]:
    out = []
    for week, s in weeks:
        state, _ = advance(state, s, week, c)
        out.append(state)
    return out


def test_one_negative_signal_for_one_week_changes_nothing_but_two_weeks_step_down() -> None:
    c = cfg()
    s1, d1 = advance(initial_state(c), sig(trend=True), 1, c)
    assert s1.equity == pytest.approx(0.90) and s1.step == 0 and d1.kind == "hold"
    s2, d2 = advance(s1, sig(trend=True), 2, c)
    assert s2.equity == pytest.approx(0.75) and s2.step == 1 and d2.kind == "derisk"


def test_fast_path_jumps_at_once_and_the_capped_remainder_continues_through_the_cooldown() -> None:
    c = cfg()
    crash = sig(True, True, True, ret=-0.12)
    states = run(initial_state(c), [(1, crash), (2, crash), (3, crash), (4, crash)], c)
    assert [round(s.equity, 6) for s in states] == [0.75, 0.60, 0.50, 0.50]
    assert states[0].step == 3


def test_a_different_signal_change_replaces_a_pending_shift() -> None:
    c = cfg()
    crash = sig(True, True, True, ret=-0.12)
    s1, _ = advance(initial_state(c), crash, 1, c)
    assert s1.pending_equity == pytest.approx(0.50)
    s2, d2 = advance(s1, sig(), 2, c)  # everything clears: re-entry, not the old shift
    assert s2.pending_equity is None and d2.kind == "reentry"


def test_cooldown_holds_a_fast_path_change_one_week_after_a_change() -> None:
    c = cfg()
    s1, _ = advance(initial_state(c), sig(trend=True, ret=-0.12), 1, c)  # step 1 at once
    assert s1.step == 1 and s1.pending_equity is None
    s2, d2 = advance(s1, sig(True, True, True, ret=-0.12), 2, c)
    assert d2.kind == "hold" and "cooldown" in " ".join(d2.reasons) and s2.equity == s1.equity
    s3, d3 = advance(s2, sig(True, True, True, ret=-0.12), 3, c)
    assert d3.kind == "derisk" and s3.equity < s1.equity


def test_a_shift_smaller_than_the_minimum_is_suppressed() -> None:
    c = cfg(
        steps=(
            LadderStep(equity=0.90, safe=0.10),
            LadderStep(equity=0.86, safe=0.14),
            LadderStep(equity=0.60, safe=0.40),
            LadderStep(equity=0.50, safe=0.50),
        ),
    )
    states = run(initial_state(c), [(1, sig(trend=True)), (2, sig(trend=True))], c)
    assert states[-1].equity == pytest.approx(0.90) and states[-1].step == 0


def test_a_five_point_shift_goes_through() -> None:
    c = cfg(
        steps=(
            LadderStep(equity=0.90, safe=0.10),
            LadderStep(equity=0.85, safe=0.15),
            LadderStep(equity=0.60, safe=0.40),
            LadderStep(equity=0.50, safe=0.50),
        ),
    )
    states = run(initial_state(c), [(1, sig(trend=True)), (2, sig(trend=True))], c)
    assert states[-1].equity == pytest.approx(0.85)


def test_reentry_from_step_two_is_four_equal_tranches_despite_the_cooldown() -> None:
    c = cfg()
    start = LadderState(equity=0.60, step=2, last_change_week=9)
    states = run(start, [(10, sig()), (11, sig()), (12, sig()), (13, sig())], c)
    assert [round(s.equity, 6) for s in states] == [0.675, 0.75, 0.825, 0.90]
    assert states[-1].step == 0 and states[-1].reentry_dest is None


def test_reentry_from_step_one_uses_tranches_below_the_minimum_shift() -> None:
    c = cfg()
    start = LadderState(equity=0.75, step=1)
    states = run(start, [(10, sig()), (11, sig()), (12, sig()), (13, sig())], c)
    assert [round(s.equity, 6) for s in states] == [0.7875, 0.825, 0.8625, 0.90]


def test_a_negative_signal_during_reentry_cancels_the_remaining_tranches() -> None:
    c = cfg()
    start = LadderState(equity=0.60, step=2)
    s1, _ = advance(start, sig(), 10, c)
    s2, _ = advance(s1, sig(), 11, c)
    assert s2.equity == pytest.approx(0.75)
    s3, d3 = advance(s2, sig(trend=True), 12, c)
    assert s3.equity == pytest.approx(0.75) and s3.reentry_dest is None
    assert d3.kind == "hold"


def test_data_gap_gives_no_recommendation_and_leaves_state_untouched() -> None:
    c = cfg()
    start = initial_state(c)
    s, d = advance(start, Signals(GAP, NEG, NEG, -0.2), 1, c)
    assert s == start and d.kind == "data_gap"


def test_no_reentry_while_the_trend_is_still_negative() -> None:
    c = cfg()
    start = LadderState(equity=0.60, step=2)
    s, d = advance(start, sig(trend=True), 10, c)  # one other signal cleared, trend has not
    assert s.equity == pytest.approx(0.60) and d.kind == "hold"


def test_destination_is_clamped_to_the_equity_floor() -> None:
    c = cfg(
        steps=(
            LadderStep(equity=0.90, safe=0.10),
            LadderStep(equity=0.75, safe=0.25),
            LadderStep(equity=0.60, safe=0.40),
            LadderStep(equity=0.40, safe=0.60),
        ),
    )
    crash = sig(True, True, True, ret=-0.15)
    states = run(initial_state(c), [(w, crash) for w in range(1, 6)], c)
    assert min(s.equity for s in states) == pytest.approx(0.50)


def test_same_inputs_give_the_same_output() -> None:
    c = cfg()
    a = advance(initial_state(c), sig(True, True), 1, c)
    b = advance(initial_state(c), sig(True, True), 1, c)
    assert a == b


def test_golden_twenty_week_sequence() -> None:
    import json
    from pathlib import Path

    g = json.loads((Path(__file__).parents[2] / "golden/ladder/twenty_weeks.json").read_text())
    c = cfg()
    state = initial_state(c)
    equity, steps = [], []
    for week, w in enumerate(g["weeks"], start=1):
        s = sig(bool(w["trend_neg"]), bool(w["dd_neg"]), bool(w["vol_neg"]), w["ret"])
        state, _ = advance(state, s, week, c)
        equity.append(round(state.equity, 6))
        steps.append(state.step)
    assert equity == pytest.approx(g["equity"])
    assert steps == g["step"]
