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


def test_turnover_is_one_way_annual_traded_over_average_value():
    dates = [date(2020, 1, 1), date(2024, 1, 1)]
    # 100000 across both legs is 50000 one way; over exactly 4 years on a 50000 average: 0.25
    assert metrics.turnover(100000.0, dates, [40000.0, 60000.0]) == pytest.approx(0.25)


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


ONE = ([date(2024, 1, 1)], [1.0])
TWO = ([date(2024, 1, 1), date(2024, 1, 2)], [1.0, 1.1])
GROWTH = [1.01**k for k in range(30)]
NOISY_FLAT = [1.0 + (1e-16 if k % 2 else 0.0) for k in range(30)]


@pytest.mark.parametrize("series", [ONE[1], TWO[1]])
def test_short_series_give_none_instead_of_raising(series):
    assert metrics.volatility(series) is None
    assert metrics.sharpe(series) is None
    assert metrics.sortino(series) is None


def test_short_series_turnover_is_none_and_cagr_is_a_clear_value_error():
    assert metrics.turnover(10.0, *ONE) is None
    assert metrics.turnover(10.0, ONE[0] * 1, [0.0]) is None
    with pytest.raises(ValueError, match="two points"):
        metrics.cagr(*ONE)
    assert metrics.calmar(*ONE) is None
    assert metrics.calmar(*TWO) is None
    assert metrics.time_to_recover(*ONE) == 0


@pytest.mark.parametrize("series", [GROWTH, NOISY_FLAT])
def test_constant_growth_and_float_noise_flat_series_have_no_ratio(series):
    assert metrics.sharpe(series) is None
    assert metrics.sortino(series) is None
    assert metrics.volatility(series) == pytest.approx(0.0, abs=1e-9)


def test_float_noise_drawdown_counts_as_no_drawdown():
    d = [date(2024, 1, k) for k in (1, 2, 3)]
    idx = [1.0, 1.0 + 1e-15, 1.0]
    assert metrics.calmar(d, idx) is None
    assert metrics.time_to_recover(d, idx) == 0
