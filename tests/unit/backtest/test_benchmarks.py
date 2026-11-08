from __future__ import annotations

from datetime import date

import pytest

from guard.backtest import benchmarks
from guard.backtest.engine import BacktestResult

DATES = [date(2024, 3, 28), date(2024, 3, 29), date(2024, 4, 1), date(2024, 4, 2)]
EQ = dict(zip(DATES, [100.0, 110.0, 110.0, 121.0], strict=True))
SF = dict.fromkeys(DATES, 100.0)


def test_registry_has_the_three_defaults():
    assert {"current_mix", "static_rebalanced", "single_tracker"} <= set(benchmarks.BENCHMARKS)


def test_current_mix_is_never_rebalanced():
    res = benchmarks.BENCHMARKS["current_mix"](EQ, SF, equity_weight=0.9, data_version="d")
    assert isinstance(res, BacktestResult)
    assert res.values[-1] == pytest.approx(90000 * 1.21 + 10000)  # 118900
    assert res.total_traded == 0


def test_static_rebalanced_quarterly_with_cost():
    res = benchmarks.BENCHMARKS["static_rebalanced"](
        EQ, SF, equity_weight=0.9, spread=0.0005, data_version="d"
    )
    # Day 1: 99000 + 10000 = 109000. First trading day of April (equity flat): rebalance to
    # 98100 / 10900, traded 2 * 900 = 1800, cost 0.9 -> 108999.1 = 98099.19 + 10899.91.
    # Day 3: 98099.19 * 1.1 + 10899.91 = 118809.019
    assert res.total_traded == pytest.approx(1800)
    assert res.values[2] == pytest.approx(108999.1)
    assert res.values[-1] == pytest.approx(118809.019)


def test_single_tracker_is_the_equity_series():
    res = benchmarks.BENCHMARKS["single_tracker"](EQ, SF, spread=0.0005, data_version="d")
    assert res.index == pytest.approx([1.0, 1.1, 1.1, 1.21])
    assert res.values[-1] == pytest.approx(121000)


def test_register_benchmark_extends_the_registry():
    def mine(eq, sf, **kw):
        return benchmarks.BENCHMARKS["single_tracker"](eq, sf, **kw)

    benchmarks.register_benchmark("mine", mine)
    try:
        assert benchmarks.BENCHMARKS["mine"] is mine
    finally:
        del benchmarks.BENCHMARKS["mine"]
