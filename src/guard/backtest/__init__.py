"""Backtest harness: replay the weekly ladder over history and measure the result.

Everything here is pure (no I/O, no clock). Inputs are two GBP level series as
``Mapping[date, float]`` (equity, safe asset); only dates present in both are used.

engine
    ``BacktestParams(mode="full"|"realistic", cadence="weekly"|"monthly", weekday=2,
    initial_value=100000, gia_share=0.4, monthly_gia_contribution=1000, spread=0.0005,
    drift_band=0.05)``; ``BacktestParams.from_config(cfg, **overrides)`` fills weekday, drift band
    and contribution from ``GuardConfig``.

    ``run_backtest(equity, safe, *, signals, ladder, params=None, data_version,
    config_version="") -> BacktestResult``. Signals on decision day D use closes before D;
    trades execute at D's close. ``signals`` and ``ladder`` are the config sections.

    ``run_fixed_weight(equity, safe, weight, *, schedule="never"|"quarterly"|"decision_days",
    params=None, data_version, config_version="", label=...) -> BacktestResult``.

    ``BacktestResult`` holds ``dates``, ``index`` (time-weighted, starts at 1.0, contributions are
    not returns), ``values`` (money), ``advised_equity``, ``total_traded``, ``decisions``
    (date, ``Decision``) pairs, final ``sleeves``, ``parameters``, ``data_version`` and a
    ``manifest`` (code_version, config_version, parameters, data_version). ``to_canonical_json()``
    is byte-identical for identical inputs. ``make_result`` builds one from plain series.

metrics (functions over ``dates`` and an ``index`` sequence)
    ``cagr``, ``max_drawdown`` (-> ``Drawdown(depth, peak, trough)``), ``time_to_recover``,
    ``volatility``, ``sharpe``, ``sortino`` (risk-free 0), ``calmar``,
    ``turnover(total_traded, dates, values)``, ``time_defensive(advised_equity, step0_equity)``,
    ``whipsaw_round_trips(decisions)``, ``rolling_lag(dates, strategy, benchmark, years)``.

benchmarks
    ``BENCHMARKS`` registry (``current_mix``, ``static_rebalanced``, ``single_tracker``), each
    ``fn(equity, safe, **options) -> BacktestResult``; ``register_benchmark(name, fn)``.

scenarios
    ``crisis_summary(strategy, buy_and_hold, floor_only, windows=DEFAULT_WINDOWS)`` ->
    ``{name: ScenarioSummary | None}``; ``floor_only_run(equity, safe, ladder, *, drift_band,
    data_version)``.
"""

from guard.backtest.benchmarks import BENCHMARKS, register_benchmark
from guard.backtest.engine import (
    BacktestParams,
    BacktestResult,
    Sleeve,
    make_result,
    run_backtest,
    run_fixed_weight,
)
from guard.backtest.scenarios import (
    DEFAULT_WINDOWS,
    CrisisWindow,
    ScenarioSummary,
    crisis_summary,
    floor_only_run,
)

__all__ = [
    "BENCHMARKS",
    "DEFAULT_WINDOWS",
    "BacktestParams",
    "BacktestResult",
    "CrisisWindow",
    "ScenarioSummary",
    "Sleeve",
    "crisis_summary",
    "floor_only_run",
    "make_result",
    "register_benchmark",
    "run_backtest",
    "run_fixed_weight",
]
