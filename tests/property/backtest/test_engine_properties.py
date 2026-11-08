from __future__ import annotations

from datetime import date

from hypothesis import given, settings
from hypothesis import strategies as st

from guard.backtest.engine import BacktestParams, run_backtest
from tests.unit.backtest.helpers import trend_only_signals, two_step_ladder, weekdays

levels = st.lists(st.floats(min_value=0.5, max_value=2.0), min_size=30, max_size=60)


def build(path: list[float]) -> tuple[dict[date, float], list[date]]:
    dates = weekdays(date(2024, 1, 1), len(path))
    out, level = {}, 100.0
    for d, step in zip(dates, path, strict=True):
        level *= step
        out[d] = level
    return out, dates


@settings(max_examples=40, deadline=None)
@given(levels, levels)
def test_weights_bounded_values_positive_and_deterministic(a, b):
    n = min(len(a), len(b))
    eq, dates = build(a[:n])
    sf, _ = build([1 + (x - 1) / 10 for x in b[:n]])
    kwargs = dict(
        signals=trend_only_signals(),
        ladder=two_step_ladder(),
        params=BacktestParams(mode="realistic", spread=0.0005),
        data_version="p",
    )
    r1 = run_backtest(eq, sf, **kwargs)
    r2 = run_backtest(eq, sf, **kwargs)
    assert r1.to_canonical_json() == r2.to_canonical_json()
    assert all(0.0 <= w <= 1.0 for w in r1.advised_equity)
    assert all(v > 0 for v in r1.values)
    assert r1.index[0] == 1.0 and all(x > 0 for x in r1.index)
    assert r1.total_traded >= 0
    assert len(r1.dates) == n
