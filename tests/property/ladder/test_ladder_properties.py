from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from guard.ladder import Signals, advance, initial_state
from guard.signals import SignalResult
from tests.unit.ladder.test_transition import cfg

_flag = st.booleans()
_ret = st.one_of(st.none(), st.floats(min_value=-0.3, max_value=0.2))
_week = st.tuples(_flag, _flag, _flag, _ret, st.booleans())


def _sig(t: bool, d: bool, v: bool, r: float | None, gap: bool) -> Signals:
    def one(neg: bool) -> SignalResult:
        return SignalResult(-0.1 if neg else 0.1, neg)

    trend = SignalResult(None, False, True) if gap else one(t)
    return Signals(trend, one(d), one(v), r)


@settings(max_examples=200, deadline=None)
@given(st.lists(_week, min_size=1, max_size=80))
def test_floor_weekly_cap_and_determinism_hold_for_any_signal_sequence(
    weeks: list[tuple[bool, bool, bool, float | None, bool]],
) -> None:
    c = cfg()
    state = initial_state(c)
    for i, w in enumerate(weeks, start=1):
        s = _sig(*w)
        new, decision = advance(state, s, i, c)
        assert new.equity >= c.equity_floor - 1e-9
        assert new.equity <= c.steps[0].equity + 1e-9
        assert abs(new.equity - state.equity) <= c.max_weekly_change + 1e-9
        assert (new, decision) == advance(state, s, i, c)
        state = new


def _run(prefix: list[tuple[bool, bool, bool, float | None, bool]], tail: Signals, n: int):  # type: ignore[no-untyped-def]
    c = cfg()
    state = initial_state(c)
    week = 0
    for w in prefix:
        week += 1
        state, _ = advance(state, _sig(*w), week, c)
    for _ in range(n):
        week += 1
        state, _ = advance(state, tail, week, c)
    return c, state


@settings(max_examples=300, deadline=None)
@given(st.lists(_week, max_size=60))
def test_six_all_negative_weeks_end_at_the_floor_whatever_came_before(
    prefix: list[tuple[bool, bool, bool, float | None, bool]],
) -> None:
    neg = SignalResult(-0.2, True)
    c, state = _run(prefix, Signals(neg, neg, neg, None), 6)
    assert state.equity == pytest.approx(max(c.steps[-1].equity, c.equity_floor))


@settings(max_examples=300, deadline=None)
@given(st.lists(_week, max_size=60))
def test_eight_all_clear_weeks_end_at_the_top_step(
    prefix: list[tuple[bool, bool, bool, float | None, bool]],
) -> None:
    ok = SignalResult(0.1, False)
    c, state = _run(prefix, Signals(ok, ok, ok, None), 8)
    assert state.equity == pytest.approx(c.steps[0].equity)
