from __future__ import annotations

import math

import pytest

from guard.signals import drawdown_signal, trend_signal, volatility_signal


def test_trend_negative_when_price_is_below_the_moving_average() -> None:
    levels = [100.0] * 4 + [90.0]  # sma(5) = 98, price 90
    s = trend_signal(levels, window=5)
    assert s.negative is True and s.data_gap is False
    assert s.value == pytest.approx(90 / 98 - 1)


def test_trend_positive_when_price_is_above_the_moving_average() -> None:
    s = trend_signal([100.0, 100.0, 100.0, 100.0, 110.0], window=5)
    assert s.negative is False
    assert s.value == pytest.approx(110 / 102 - 1)


def test_trend_with_too_little_history_is_a_data_gap_not_a_guess() -> None:
    s = trend_signal([100.0, 101.0], window=5)
    assert s.data_gap is True and s.value is None and s.negative is False


def test_trend_price_exactly_on_the_average_is_not_negative() -> None:
    assert trend_signal([100.0, 100.0, 100.0], window=3).negative is False


def test_drawdown_measures_distance_below_the_trailing_high() -> None:
    levels = [100.0, 120.0, 110.0, 108.0]
    s = drawdown_signal(levels, lookback=4, threshold=0.10)
    assert s.value == pytest.approx(108 / 120 - 1)
    assert s.negative is True  # -10.0% is at the threshold


def test_drawdown_inside_the_threshold_is_not_negative() -> None:
    s = drawdown_signal([100.0, 120.0, 115.0], lookback=3, threshold=0.10)
    assert s.negative is False


def test_drawdown_ignores_highs_older_than_the_lookback() -> None:
    s = drawdown_signal([200.0, 100.0, 100.0, 100.0], lookback=3, threshold=0.10)
    assert s.value == 0.0 and s.negative is False


def test_drawdown_empty_history_is_a_data_gap() -> None:
    assert drawdown_signal([], lookback=3, threshold=0.1).data_gap is True


def test_volatility_is_annualised_stdev_of_daily_log_returns() -> None:
    levels = [100.0, 101.0, 100.0, 101.0, 100.0]
    rets = [math.log(b / a) for a, b in zip(levels, levels[1:], strict=False)]
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
    s = volatility_signal(levels, window=4, threshold=0.10)
    assert s.value == pytest.approx(sd * math.sqrt(252))
    assert s.negative is True


def test_volatility_below_threshold_is_not_negative() -> None:
    s = volatility_signal([100.0 + 0.01 * i for i in range(30)], window=20, threshold=0.25)
    assert s.negative is False


def test_volatility_needs_window_plus_one_levels() -> None:
    assert volatility_signal([100.0, 101.0], window=5, threshold=0.2).data_gap is True
