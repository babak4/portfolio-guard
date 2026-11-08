"""The weekly computation, shared by the live run and by replay: signals, ladder, advice.

Pure. Everything the answer depends on is a field of ``WeeklyInputs``, so a stored copy of the
inputs recomputes the same answer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from guard.advice import Advice, AdviceInputs, FundRegistry
from guard.config import GuardConfig
from guard.config.models import Signals as SignalsConfig
from guard.guardrails import advise
from guard.ingest.model import Holding
from guard.ladder import Decision, LadderState, Signals, advance
from guard.signals import drawdown_signal, trend_signal, trend_signal_months, volatility_signal

# How many of the latest closes the signals and the audit record use. It covers the longest
# window (a year of weeks at five days each, or a 200-day average) with room to spare.
CLOSES_TAIL = 300
TRADING_DAYS_PER_WEEK = 5
EPOCH_MONDAY = date(2000, 1, 3)


def week_number(slot: date) -> int:
    """Whole calendar weeks since a fixed Monday, so the same week always has the same number."""
    return (slot - EPOCH_MONDAY).days // 7


@dataclass(frozen=True)
class WeeklyInputs:
    config: GuardConfig
    days: tuple[date, ...]
    levels: tuple[float, ...]
    state_before: LadderState
    week: int
    holdings: tuple[Holding, ...]
    cash: Mapping[str, Decimal]
    contributions: Mapping[str, Decimal]
    funds: FundRegistry
    paused: bool = False
    available_symbols: frozenset[str] | None = None


@dataclass(frozen=True)
class WeeklyComputation:
    signals: Signals
    state_after: LadderState
    decision: Decision
    advice: Advice


def week_return(levels: tuple[float, ...] | list[float]) -> float | None:
    if len(levels) <= TRADING_DAYS_PER_WEEK:
        return None
    return levels[-1] / levels[-1 - TRADING_DAYS_PER_WEEK] - 1.0


def compute_signals(
    cfg: SignalsConfig, days: tuple[date, ...], levels: tuple[float, ...]
) -> Signals:
    """The three signals and the week's return on the latest ``CLOSES_TAIL`` closes."""
    days, levels = days[-CLOSES_TAIL:], levels[-CLOSES_TAIL:]
    if cfg.trend_method == "sma_days":
        trend = trend_signal(levels, cfg.trend_window)
    else:
        trend = trend_signal_months(days, levels, cfg.trend_window)
    drawdown = drawdown_signal(
        levels, cfg.drawdown_lookback_weeks * TRADING_DAYS_PER_WEEK, cfg.drawdown_threshold
    )
    volatility = volatility_signal(levels, cfg.volatility_window_days, cfg.volatility_threshold)
    return Signals(trend, drawdown, volatility, week_return(levels))


def compute_weekly(inputs: WeeklyInputs) -> WeeklyComputation:
    cfg = inputs.config
    signals = compute_signals(cfg.signals, inputs.days, inputs.levels)
    state_after, decision = advance(inputs.state_before, signals, inputs.week, cfg.ladder)
    advice = advise(
        AdviceInputs(
            holdings=inputs.holdings,
            cash=inputs.cash,
            equity_before=decision.equity_before,
            equity_after=decision.equity_after,
            contributions=inputs.contributions,
            funds=inputs.funds,
            config=cfg,
            paused=inputs.paused,
            available_symbols=inputs.available_symbols,
        )
    )
    return WeeklyComputation(signals, state_after, decision, advice)
