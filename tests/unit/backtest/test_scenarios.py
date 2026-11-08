from __future__ import annotations

from datetime import date

import pytest

from guard.backtest.engine import BacktestResult, make_result
from guard.backtest.scenarios import CrisisWindow, crisis_summary, floor_only_run
from tests.unit.backtest.helpers import two_step_ladder

DATES = [
    date(2019, 12, 1),
    date(2020, 2, 3),
    date(2020, 3, 2),
    date(2020, 4, 1),
    date(2020, 5, 1),
    date(2021, 1, 1),
]


def res(index: list[float]) -> BacktestResult:
    return make_result(DATES, index)


W = [
    CrisisWindow("covid", date(2020, 2, 1), date(2020, 12, 31)),
    CrisisWindow("gfc", date(2008, 1, 1), date(2009, 6, 30)),
]


def test_window_summary_ignores_points_outside_the_window():
    strat = res([5.0, 1.0, 0.9, 0.8, 0.9, 0.1])
    held = res([5.0, 1.0, 0.8, 0.6, 1.0, 0.1])
    floor = res([5.0, 1.0, 0.85, 0.7, 0.9, 0.1])
    out = crisis_summary(strat, held, floor, W)
    s = out["covid"]
    assert s is not None
    assert s.achieved_max_drawdown == pytest.approx(-0.2)
    assert s.buy_and_hold_max_drawdown == pytest.approx(-0.4)
    assert s.floor_only_max_drawdown == pytest.approx(-0.3)
    # buy-and-hold returns 0% in the window, the strategy -10%: it lags by 10 points
    assert s.return_lag == pytest.approx(0.1)


def test_window_with_no_data_is_none_not_zero():
    strat = res([1.0] * 6)
    out = crisis_summary(strat, strat, strat, W)
    assert out["gfc"] is None


def test_default_windows_are_the_three_crises():
    strat = res([1.0] * 6)
    out = crisis_summary(strat, strat, strat)
    assert list(out) == ["2008-2009", "2020", "2022"]
    assert out["2022"] is None


def test_floor_only_run_holds_the_floor_weight_with_decision_day_rebalancing():
    from tests.unit.backtest.helpers import weekdays

    dates = weekdays(date(2024, 1, 1), 10)
    eq = dict(zip(dates, [100.0] * 10, strict=True))
    sf = dict.fromkeys(dates, 100.0)
    out = floor_only_run(eq, sf, two_step_ladder(floor=0.5), drift_band=0.05, data_version="d")
    assert set(out.advised_equity) == {0.5}
    assert out.values[-1] == pytest.approx(100000)
