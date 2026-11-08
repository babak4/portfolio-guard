"""Benchmark simulations over the same inputs as the engine, in a registry that can be extended.

Every benchmark is called as ``fn(equity, safe, **options)`` and returns a ``BacktestResult``, so
the same metrics apply to it. Common options: ``initial_value``, ``spread``, ``weekday``,
``drift_band``, ``data_version``, ``config_version``. Benchmarks start fully invested with no cost.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from guard.backtest.engine import BacktestParams, BacktestResult, Series, run_fixed_weight

Benchmark = Callable[..., BacktestResult]


def _run(
    name: str,
    equity: Series,
    safe: Series,
    weight: float,
    schedule: Any,
    options: dict[str, Any],
) -> BacktestResult:
    data_version = options.pop("data_version", "")
    config_version = options.pop("config_version", "")
    return run_fixed_weight(
        equity,
        safe,
        weight,
        schedule=schedule,
        params=BacktestParams(**options),
        data_version=data_version,
        config_version=config_version,
        label=name,
    )


def current_mix(
    equity: Series, safe: Series, *, equity_weight: float = 0.9, **options: Any
) -> BacktestResult:
    """Buy and hold the given weights; never rebalanced."""
    return _run("current_mix", equity, safe, equity_weight, "never", options)


def static_rebalanced(
    equity: Series, safe: Series, *, equity_weight: float = 0.9, **options: Any
) -> BacktestResult:
    """A fixed equity weight rebalanced at the first close of each calendar quarter."""
    return _run("static_rebalanced", equity, safe, equity_weight, "quarterly", options)


def single_tracker(equity: Series, safe: Series, **options: Any) -> BacktestResult:
    """One hundred per cent in the equity series."""
    return _run("single_tracker", equity, safe, 1.0, "never", options)


BENCHMARKS: dict[str, Benchmark] = {
    "current_mix": current_mix,
    "static_rebalanced": static_rebalanced,
    "single_tracker": single_tracker,
}


def register_benchmark(name: str, fn: Benchmark) -> None:
    """Add or replace a benchmark in the registry."""
    BENCHMARKS[name] = fn
