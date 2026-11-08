from __future__ import annotations

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
