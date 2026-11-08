"""Parameter sweeps: a declarative grid, one trial per grid point, every trial on record.

The grid and the study settings live in ``guard/config/defaults/backtest_grid.yaml``. Each trial
runs the strategy with the overridden parameters and keeps headline metrics plus the hash of the
result's manifest, so a trial can be found again and reproduced. Every trial is appended to a
JSON-lines log, which makes the number of trials tried a matter of record.
"""

from __future__ import annotations

import bisect
import hashlib
import itertools
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from guard.backtest import metrics as m
from guard.backtest.benchmarks import BENCHMARKS
from guard.backtest.engine import (
    BacktestParams,
    BacktestResult,
    Series,
    run_backtest,
)
from guard.backtest.metrics import DAYS_PER_YEAR
from guard.config.models import GuardConfig, Ladder, Signals

Params = dict[str, Any]
AXES = (
    "trend_window",
    "drawdown_threshold",
    "volatility_threshold",
    "confirmation_runs",
    "cadence",
    "weekday",
    "mode",
    "safe_asset",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Targets(_Strict):
    crisis_max_drawdown: float
    annual_lag_pct: float
    lag_window_years: int


class PlateauSettings(_Strict):
    tolerance: float
    floors: dict[str, float]
    objective: str
    pairs: list[tuple[str, str]]


class WalkForwardSettings(_Strict):
    train_years: int
    test_years: int
    min_test_days: int
    objective: str
    tie_tolerance: float
    pinned: dict[str, Any]


class WeekdaySettings(_Strict):
    offsets: int
    metric: str


class DataSettings(_Strict):
    rate_series_max_gap_days: int


class BacktestSettings(_Strict):
    grid: dict[str, list[Any]]
    quick_grid: dict[str, list[Any]]
    targets: Targets
    plateau: PlateauSettings
    walk_forward: WalkForwardSettings
    weekday_offsets: WeekdaySettings
    data: DataSettings


def load_settings() -> BacktestSettings:
    text = resources.files("guard.config").joinpath("defaults/backtest_grid.yaml").read_text()
    return BacktestSettings.model_validate(yaml.safe_load(text))


def expand_grid(grid: Mapping[str, Sequence[Any]]) -> list[Params]:
    """The cartesian product of the axes, first axis varying slowest."""
    keys = list(grid)
    return [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*grid.values())]


@dataclass(frozen=True)
class TrialResult:
    """One trial: its parameters, headline metrics and the manifest hash of the run."""

    params: Params
    metrics: dict[str, float | None]
    manifest_hash: str

    def to_record(self, trial: int, stage: str) -> dict[str, Any]:
        return {
            "trial": trial,
            "stage": stage,
            "params": self.params,
            "metrics": self.metrics,
            "manifest_hash": self.manifest_hash,
        }


class TrialLog:
    """Append-only JSON-lines record of every trial run; ``count`` is the number of lines."""

    def __init__(self, path: Path | str, *, reset: bool = False) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if reset or not self.path.exists():
            self.path.write_text("")
        self.count = len(self.path.read_text().splitlines())

    def record(self, trial: TrialResult, stage: str) -> None:
        self.count += 1
        line = json.dumps(trial.to_record(self.count, stage), sort_keys=True, default=repr)
        with self.path.open("a") as f:
            f.write(line + "\n")


def as_log(log: TrialLog | Path | str | None) -> TrialLog | None:
    if log is None or isinstance(log, TrialLog):
        return log
    return TrialLog(log)


def default_params(cfg: GuardConfig, safe_asset: str | None = None) -> Params:
    """The configured setting on every axis (the tie-break anchor for parameter choice)."""
    out: Params = {
        "trend_window": cfg.signals.trend_window,
        "drawdown_threshold": cfg.signals.drawdown_threshold,
        "volatility_threshold": cfg.signals.volatility_threshold,
        "confirmation_runs": cfg.ladder.confirmation_runs,
        "cadence": "weekly",
        "weekday": BacktestParams.from_config(cfg).weekday,
        "mode": "full",
    }
    if safe_asset is not None:
        out["safe_asset"] = safe_asset
    return out


def apply_params(
    cfg: GuardConfig, params: Mapping[str, Any]
) -> tuple[Signals, Ladder, BacktestParams]:
    """Signals, ladder and run parameters with the named axes overridden."""
    unknown = set(params) - set(AXES)
    if unknown:
        raise ValueError(f"unknown parameter: {sorted(unknown)}")
    signal_axes = ("trend_window", "drawdown_threshold", "volatility_threshold")
    signals = cfg.signals.model_copy(update={k: params[k] for k in signal_axes if k in params})
    ladder = cfg.ladder
    if "confirmation_runs" in params:
        ladder = ladder.model_copy(update={"confirmation_runs": params["confirmation_runs"]})
    run_over = {k: params[k] for k in ("cadence", "weekday", "mode") if k in params}
    return signals, ladder, BacktestParams.from_config(cfg, **run_over)


def rolling_lag_fast(
    dates: Sequence[date], strategy: Sequence[float], benchmark: Sequence[float], years: int
) -> list[tuple[date, float]]:
    """Same result as ``metrics.rolling_lag``, with bisection instead of linear scans."""
    month_ends = [
        d
        for i, d in enumerate(dates)
        if i + 1 == len(dates) or (dates[i + 1].year, dates[i + 1].month) != (d.year, d.month)
    ]
    out: list[tuple[date, float]] = []
    for end in month_ends:
        start = m._years_back(end, years)
        if start < dates[0]:
            continue
        i0 = bisect.bisect_right(dates, start) - 1
        i1 = bisect.bisect_right(dates, end) - 1
        span = (end - start).days / DAYS_PER_YEAR
        b = (benchmark[i1] / benchmark[i0]) ** (1 / span)
        s = (strategy[i1] / strategy[i0]) ** (1 / span)
        out.append((end, (b - s) * 100.0))
    return out


def headline_metrics(
    result: BacktestResult, benchmark: BacktestResult, *, step0_equity: float, lag_years: int
) -> dict[str, float | None]:
    """Headline metrics of ``result`` against ``benchmark`` (a buy-and-hold on the same dates).

    ``worst_lag`` is the largest rolling-window lag in percent a year (positive: trailed).
    """
    years = (result.dates[-1] - result.dates[0]).days / DAYS_PER_YEAR
    lags = rolling_lag_fast(result.dates, result.index, benchmark.index, lag_years)
    return {
        "cagr": m.cagr(result.dates, result.index),
        "max_drawdown": m.max_drawdown(result.dates, result.index).depth,
        "calmar": m.calmar(result.dates, result.index),
        "sortino": m.sortino(result.index),
        "turnover": m.turnover(result.total_traded, result.dates, result.values),
        "whipsaws_per_year": m.whipsaw_round_trips(result.decisions) / years if years > 0 else 0.0,
        "time_defensive": m.time_defensive(result.advised_equity, step0_equity),
        "worst_lag": max((v for _, v in lags), default=None),
    }


def manifest_hash(result: BacktestResult) -> str:
    text = json.dumps(result.manifest, sort_keys=True, separators=(",", ":"), default=repr)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def buy_and_hold(
    equity: Series, safe: Series, cfg: GuardConfig, *, data_version: str, config_version: str = ""
) -> BacktestResult:
    """The reference portfolio: the ladder's step-0 mix, never rebalanced."""
    return BENCHMARKS["current_mix"](
        equity,
        safe,
        equity_weight=cfg.ladder.steps[0].equity,
        data_version=data_version,
        config_version=config_version,
    )


def run_trial(
    equity: Series,
    safe: Series,
    params: Mapping[str, Any],
    cfg: GuardConfig,
    *,
    benchmark: BacktestResult,
    data_version: str,
    config_version: str = "",
    lag_years: int = 3,
) -> tuple[TrialResult, BacktestResult]:
    """Run one parameter set. ``params`` may include ``safe_asset`` (a label only)."""
    signals, ladder, bp = apply_params(cfg, params)
    result = run_backtest(
        equity,
        safe,
        signals=signals,
        ladder=ladder,
        params=bp,
        data_version=data_version,
        config_version=config_version,
    )
    metrics = headline_metrics(
        result, benchmark, step0_equity=ladder.steps[0].equity, lag_years=lag_years
    )
    return TrialResult(dict(params), metrics, manifest_hash(result)), result


def run_sweep(
    equity: Series,
    safes: Mapping[str, Series],
    grid: Mapping[str, Sequence[Any]],
    cfg: GuardConfig,
    *,
    trial_log: TrialLog | Path | str,
    data_version: str = "",
    config_version: str = "",
    lag_years: int = 3,
    progress: Callable[[int, int], None] | None = None,
) -> list[TrialResult]:
    """Run every grid point against every named safe series, logging each trial.

    If ``grid`` has no ``safe_asset`` axis, every key of ``safes`` is swept. Lag is measured
    against buy-and-hold on the same safe series.
    """
    if not safes:
        raise ValueError("need at least one named safe series")
    unknown = set(grid) - set(AXES)
    if unknown:
        raise ValueError(f"unknown parameter: {sorted(unknown)}")
    full = dict(grid)
    full.setdefault("safe_asset", list(safes))
    missing = set(full["safe_asset"]) - set(safes)
    if missing:
        raise ValueError(f"safe series not provided: {sorted(missing)}")
    log = as_log(trial_log)
    assert log is not None
    points = expand_grid(full)
    bench: dict[str, BacktestResult] = {}
    out: list[TrialResult] = []
    for i, params in enumerate(points):
        name = params["safe_asset"]
        dv = f"{data_version}:{name}"
        if name not in bench:
            bench[name] = buy_and_hold(equity, safes[name], cfg, data_version=dv)
        trial, _ = run_trial(
            equity,
            safes[name],
            params,
            cfg,
            benchmark=bench[name],
            data_version=dv,
            config_version=config_version,
            lag_years=lag_years,
        )
        log.record(trial, "sweep")
        out.append(trial)
        if progress is not None:
            progress(i + 1, len(points))
    return out
