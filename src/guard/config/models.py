"""Typed configuration. Unknown keys are rejected, so a typo cannot fall back to a default."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Schedule(_Strict):
    timezone: str
    weekly_weekday: Literal["monday", "tuesday", "wednesday", "thursday", "friday"]
    weekly_time: str
    daily_check_time: str


class Signals(_Strict):
    trend_method: Literal["sma_days", "sma_months"]
    trend_window: int = Field(gt=0)
    drawdown_threshold: float = Field(gt=0, lt=1)
    drawdown_lookback_weeks: int = Field(gt=0)
    volatility_window_days: int = Field(gt=1)
    volatility_threshold: float = Field(gt=0)


class LadderStep(_Strict):
    equity: float = Field(ge=0, le=1)
    safe: float = Field(ge=0, le=1)


class Ladder(_Strict):
    steps: tuple[LadderStep, ...]
    confirmation_runs: int = Field(ge=1)
    fast_path_weekly_drop: float = Field(gt=0, lt=1)
    min_shift: float = Field(ge=0, lt=1)
    cooldown_weeks: int = Field(ge=0)
    max_weekly_change: float = Field(gt=0, le=1)
    equity_floor: float = Field(ge=0, le=1)
    reentry_tranches: int = Field(ge=1)


class DailyCheck(_Strict):
    day_drop: float = Field(gt=0, lt=1)
    week_drop: float = Field(gt=0, lt=1)


class Rebalance(_Strict):
    drift_band: float = Field(gt=0, lt=1)


class Constraints(_Strict):
    us_exposure_cap: float = Field(gt=0, le=1)
    fee_ceiling: float = Field(gt=0, lt=1)


class DataRules(_Strict):
    jump_threshold: float = Field(gt=0, lt=1)
    crosscheck_tolerance: float = Field(gt=0, lt=1)
    crosscheck_min_overlap: float = Field(gt=0, le=1)
    stale_trading_days: int = Field(ge=0)
    max_gap_days: int = Field(gt=0)


class Account(_Strict):
    id: str
    kind: Literal["isa", "gia"]
    platform: str


class Portfolio(_Strict):
    base_currency: str
    gia_monthly_contribution: int = Field(ge=0)
    fixed_satellites: tuple[str, ...]
    safe_asset: str
    accounts: tuple[Account, ...]


class Benchmark(_Strict):
    id: str
    kind: Literal["buy_and_hold_current", "static_rebalanced", "single_tracker"]
    equity: float | None = None


class Alerts(_Strict):
    detail: Literal["minimal", "full"]


class GuardConfig(_Strict):
    schedule: Schedule
    signals: Signals
    ladder: Ladder
    daily_check: DailyCheck
    rebalance: Rebalance
    constraints: Constraints
    data: DataRules
    portfolio: Portfolio
    benchmarks: tuple[Benchmark, ...]
    alerts: Alerts
