from __future__ import annotations

import math
from datetime import date

import pytest

from guard.backtest import metrics
from guard.ladder import Decision

D = [
    date(2020, 1, 1),
    date(2020, 7, 1),
    date(2020, 10, 1),
    date(2021, 1, 1),
    date(2021, 7, 1),
    date(2022, 1, 1),
    date(2024, 1, 1),
]
IDX = [1.0, 2.0, 1.5, 0.75, 1.5, 2.0, 16.0]


def test_cagr_over_exactly_four_years():
    # 2020-01-01 to 2024-01-01 is 1461 days = 4 * 365.25; 16 ** (1/4) - 1 = 1
    assert metrics.cagr(D, IDX) == pytest.approx(1.0)


def test_max_drawdown_dates_and_recovery():
    dd = metrics.max_drawdown(D, IDX)
    assert dd.depth == pytest.approx(-0.625)
    assert (dd.peak, dd.trough) == (date(2020, 7, 1), date(2021, 1, 1))
    assert metrics.time_to_recover(D, IDX) == 365  # trough 2021-01-01, back to 2.0 on 2022-01-01


def test_never_recovered_is_none():
    assert metrics.time_to_recover(D[:4], IDX[:4]) is None


def test_calmar():
    assert metrics.calmar(D, IDX) == pytest.approx(1.0 / 0.625)


def test_no_drawdown():
    dd = metrics.max_drawdown(D[:2], [1.0, 2.0])
    assert dd.depth == 0.0
    assert metrics.calmar(D[:2], [1.0, 2.0]) is None
    assert metrics.time_to_recover(D[:2], [1.0, 2.0]) == 0


SHORT_D = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
SHORT = [1.0, 1.1, 0.99, 1.089]  # returns +10%, -10%, +10%


def test_volatility_sharpe_sortino_hand_values():
    assert metrics.volatility(SHORT) == pytest.approx(0.2 * math.sqrt(84))
    assert metrics.sharpe(SHORT) == pytest.approx(math.sqrt(756) / 6)
    assert metrics.sortino(SHORT) == pytest.approx(math.sqrt(84))


def test_zero_denominators_give_none():
    assert metrics.sortino([1.0, 1.1, 1.2]) is None
    assert metrics.sharpe([1.0, 1.0, 1.0]) is None


def test_turnover_is_annual_traded_over_average_value():
    dates = [date(2020, 1, 1), date(2024, 1, 1)]
    assert metrics.turnover(100000.0, dates, [40000.0, 60000.0]) == pytest.approx(0.5)


def test_time_defensive():
    assert metrics.time_defensive([0.9, 0.9, 0.75, 0.6], 0.9) == pytest.approx(0.5)


def dec(kind):
    return Decision(kind, 0.8, 0.6, 1)


def test_one_whipsaw_is_counted_once_and_slow_cycle_is_not():
    fast = [
        (date(2024, 1, 3), dec("derisk")),
        (date(2024, 1, 10), dec("continue")),
        (date(2024, 1, 31), dec("reentry")),  # 21 days after the last reduction
        (date(2024, 2, 7), dec("reentry")),
        (date(2024, 3, 6), dec("derisk")),
        (date(2024, 5, 8), dec("reentry")),  # slow: 63 days
    ]
    assert metrics.whipsaw_round_trips(fast) == 1
    assert metrics.whipsaw_round_trips(fast[4:]) == 0


def test_whipsaw_boundary_is_four_weeks_inclusive():
    on = [(date(2024, 1, 3), dec("derisk")), (date(2024, 1, 31), dec("reentry"))]
    off = [(date(2024, 1, 3), dec("derisk")), (date(2024, 2, 7), dec("reentry"))]
    assert metrics.whipsaw_round_trips(on) == 1
    assert metrics.whipsaw_round_trips(off) == 0


def test_rolling_lag_in_percent_per_year():
    dates = [date(2021, 1, 1), date(2022, 1, 1), date(2023, 1, 1)]
    out = metrics.rolling_lag(dates, [1.0, 1.05, 1.1025], [1.0, 1.10, 1.21], years=1)
    assert [d for d, _ in out] == [date(2022, 1, 1), date(2023, 1, 1)]
    for _, lag in out:
        assert lag == pytest.approx(5.0, abs=0.1)  # 10% benchmark vs 5% strategy
    same = metrics.rolling_lag(dates, [1.0, 1.1, 1.2], [1.0, 1.1, 1.2], years=1)
    assert [x for _, x in same] == [0.0, 0.0]
    assert metrics.rolling_lag(dates, [1.0, 1.1, 1.2], [1.0, 1.1, 1.2], years=5) == []
