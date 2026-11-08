"""Daily-resolution simulator that replays the weekly ladder over two aligned GBP level series.

Pure: no I/O, no clock, no randomness. Identical inputs give byte-identical canonical output.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Literal

from guard.config.models import GuardConfig, Ladder, Signals
from guard.ladder import Decision, LadderState, advance, initial_state
from guard.ladder import Signals as LadderSignals
from guard.market.history import History, HistoryPoint, weekly
from guard.signals import (
    drawdown_signal,
    trend_signal,
    trend_signal_months,
    volatility_signal,
)

CODE_VERSION = "backtest-1"
TRADING_DAYS_PER_WEEK = 5
_EPS = 1e-12

Series = Mapping[date, float]
Mode = Literal["full", "realistic"]
Cadence = Literal["weekly", "monthly"]
Schedule = Literal["never", "quarterly", "decision_days"]

_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4}


@dataclass(frozen=True)
class BacktestParams:
    """Run parameters. ``weekday`` is 0 (Monday) to 4 (Friday).

    ``spread`` is a fraction of traded value (buy plus sell legs) charged on every rebalance.
    ``drift_band`` is the allowed gap between actual and advised equity weight on a decision day
    when the ladder did not move. ``gia_share`` and ``monthly_gia_contribution`` only apply in
    ``realistic`` mode.
    """

    mode: Mode = "full"
    cadence: Cadence = "weekly"
    weekday: int = 2
    initial_value: float = 100000.0
    gia_share: float = 0.4
    monthly_gia_contribution: float = 1000.0
    spread: float = 0.0005
    drift_band: float = 0.05

    @classmethod
    def from_config(cls, cfg: GuardConfig, **overrides: Any) -> BacktestParams:
        """Defaults taken from the configuration (weekday, drift band, GIA contribution)."""
        base: dict[str, Any] = {
            "weekday": _WEEKDAYS[cfg.schedule.weekly_weekday],
            "drift_band": cfg.rebalance.drift_band,
            "monthly_gia_contribution": float(cfg.portfolio.gia_monthly_contribution),
        }
        base.update(overrides)
        return cls(**base)


@dataclass(frozen=True)
class Sleeve:
    """One pot of money. A non-tradable sleeve only changes through returns and contributions."""

    name: str
    tradable: bool
    equity: float
    safe: float

    @property
    def value(self) -> float:
        return self.equity + self.safe


@dataclass(frozen=True)
class BacktestResult:
    """Everything a run produced. All per-day sequences align with ``dates``.

    ``index`` is the time-weighted index (starts at 1.0; contributions are not returns).
    ``values`` is the total money value. ``advised_equity`` is the advised weight in force at
    the close. ``decisions`` lists each ladder decision with its date. ``sleeves`` are the final
    balances. ``manifest`` identifies the run reproducibly.
    """

    dates: tuple[date, ...]
    index: tuple[float, ...]
    values: tuple[float, ...]
    advised_equity: tuple[float, ...]
    total_traded: float = 0.0
    decisions: tuple[tuple[date, Decision], ...] = ()
    sleeves: tuple[Sleeve, ...] = ()
    parameters: dict[str, Any] = field(default_factory=dict)
    data_version: str = ""
    manifest: dict[str, Any] = field(default_factory=dict)

    def to_canonical_json(self) -> str:
        """Deterministic text of the manifest and every result series (floats via repr)."""
        body = {
            "manifest": self.manifest,
            "dates": [d.isoformat() for d in self.dates],
            "index": [repr(x) for x in self.index],
            "values": [repr(x) for x in self.values],
            "advised_equity": [repr(x) for x in self.advised_equity],
            "total_traded": repr(self.total_traded),
            "decisions": [[d.isoformat(), asdict(x)] for d, x in self.decisions],
            "sleeves": [asdict(s) for s in self.sleeves],
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":"), default=repr)


def make_result(
    dates: Sequence[date],
    index: Sequence[float],
    *,
    values: Sequence[float] | None = None,
    advised_equity: Sequence[float] | None = None,
    total_traded: float = 0.0,
    decisions: Sequence[tuple[date, Decision]] = (),
    sleeves: Sequence[Sleeve] = (),
    parameters: Mapping[str, Any] | None = None,
    data_version: str = "",
    config_version: str = "",
    code_version: str = CODE_VERSION,
) -> BacktestResult:
    """Assemble a result and its manifest. ``values`` defaults to ``index``."""
    params = dict(parameters or {})
    manifest = {
        "code_version": code_version,
        "config_version": config_version,
        "parameters": params,
        "data_version": data_version,
    }
    vals = tuple(values if values is not None else index)
    return BacktestResult(
        dates=tuple(dates),
        index=tuple(index),
        values=vals,
        advised_equity=tuple(advised_equity if advised_equity is not None else [0.0] * len(vals)),
        total_traded=total_traded,
        decisions=tuple(decisions),
        sleeves=tuple(sleeves),
        parameters=params,
        data_version=data_version,
        manifest=manifest,
    )


def align(equity: Series, safe: Series) -> tuple[list[date], list[float], list[float]]:
    """Dates present in both series, ascending, with their levels."""
    dates = sorted(set(equity) & set(safe))
    if len(dates) < 2:
        raise ValueError("need at least two dates present in both series")
    eq = [float(equity[d]) for d in dates]
    sf = [float(safe[d]) for d in dates]
    if min(eq) <= 0 or min(sf) <= 0:
        raise ValueError("levels must be positive")
    return dates, eq, sf


def decision_day_indices(dates: Sequence[date], weekday: int) -> list[int]:
    """Indices of each ISO week's last trading day on or before ``weekday`` (the history rule)."""
    history = History(tuple(HistoryPoint(d, 1.0, "etf", "calendar") for d in dates))
    wanted = {p.day for p in weekly(history, weekday)}
    return [i for i, d in enumerate(dates) if d in wanted]


def _first_of_month(dates: Sequence[date], indices: Sequence[int]) -> list[int]:
    seen: set[tuple[int, int]] = set()
    out: list[int] = []
    for i in indices:
        key = (dates[i].year, dates[i].month)
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out


def _rebalance(sleeve: Sleeve, weight: float, spread: float) -> tuple[Sleeve, float]:
    """Trade to ``weight``. Returns the new sleeve and the traded value (buy plus sell legs)."""
    value = sleeve.value
    traded = 2.0 * abs(weight * value - sleeve.equity)
    after = value - spread * traded
    return Sleeve(sleeve.name, sleeve.tradable, weight * after, (1.0 - weight) * after), traded


def _needs_trade(sleeve: Sleeve, weight: float, moved: bool, band: float) -> bool:
    value = sleeve.value
    return value > 0 and (moved or abs(sleeve.equity / value - weight) > band + _EPS)


def _signals_at(
    i: int, dates: Sequence[date], eq: Sequence[float], cfg: Signals, ladder_days: int
) -> LadderSignals:
    """Signals from closes strictly before index ``i``."""
    drawdown_days = cfg.drawdown_lookback_weeks * TRADING_DAYS_PER_WEEK
    if cfg.trend_method == "sma_days":
        need = max(cfg.trend_window, drawdown_days, cfg.volatility_window_days + 1, ladder_days)
        tail = eq[max(0, i - need) : i]
        trend = trend_signal(tail, cfg.trend_window)
    else:
        tail = eq[max(0, i - max(drawdown_days, cfg.volatility_window_days + 1, ladder_days)) : i]
        trend = trend_signal_months(dates[:i], eq[:i], cfg.trend_window)
    drawdown = drawdown_signal(tail, drawdown_days, cfg.drawdown_threshold)
    volatility = volatility_signal(tail, cfg.volatility_window_days, cfg.volatility_threshold)
    weekly_return: float | None = None
    if i - 1 - TRADING_DAYS_PER_WEEK >= 0:
        weekly_return = eq[i - 1] / eq[i - 1 - TRADING_DAYS_PER_WEEK] - 1.0
    return LadderSignals(trend, drawdown, volatility, weekly_return)


def _parameters(
    params: BacktestParams, signals: Signals | None = None, ladder: Ladder | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = asdict(params)
    if signals is not None:
        out["signals"] = signals.model_dump(mode="json")
    if ladder is not None:
        out["ladder"] = ladder.model_dump(mode="json")
    return out


def run_backtest(
    equity: Series,
    safe: Series,
    *,
    signals: Signals,
    ladder: Ladder,
    params: BacktestParams | None = None,
    data_version: str,
    config_version: str = "",
    code_version: str = CODE_VERSION,
) -> BacktestResult:
    """Replay the ladder over ``equity`` and ``safe`` (date -> GBP level).

    On each decision day D the signals use only closes dated before D, and trades execute at D's
    close. Between decisions weights drift with returns. In ``realistic`` mode the ISA sleeve
    follows the ladder while the GIA sleeve never sells; each month's contribution is invested at
    the previous close split by the advised equity weight.
    """
    p = params or BacktestParams()
    dates, eq, sf = align(equity, safe)
    n = len(dates)

    state: LadderState = initial_state(ladder)
    if p.mode == "realistic":
        sleeves = [
            Sleeve("isa", True, 0.0, 0.0),
            Sleeve("gia", False, 0.0, 0.0),
        ]
        shares = [1.0 - p.gia_share, p.gia_share]
    else:
        sleeves = [Sleeve("all", True, 0.0, 0.0)]
        shares = [1.0]
    sleeves = [
        Sleeve(
            s.name,
            s.tradable,
            state.equity * sh * p.initial_value,
            (1 - state.equity) * sh * p.initial_value,
        )
        for s, sh in zip(sleeves, shares, strict=True)
    ]

    days = decision_day_indices(dates, p.weekday)
    if p.cadence == "monthly":
        days = _first_of_month(dates, days)
    decision_set = {i for i in days if i >= 1}

    index = [1.0]
    values = [sum(s.value for s in sleeves)]
    advised = [state.equity]
    decisions: list[tuple[date, Decision]] = []
    traded_total = 0.0
    week = 0
    longest = TRADING_DAYS_PER_WEEK + 1

    for t in range(1, n):
        contribution = 0.0
        if (
            p.mode == "realistic"
            and (dates[t].year, dates[t].month) != (dates[t - 1].year, dates[t - 1].month)
            and p.monthly_gia_contribution > 0
        ):
            contribution = p.monthly_gia_contribution
            gia = sleeves[1]
            sleeves[1] = Sleeve(
                gia.name,
                gia.tradable,
                gia.equity + contribution * state.equity,
                gia.safe + contribution * (1.0 - state.equity),
            )
        prev_total = sum(s.value for s in sleeves)
        r_eq, r_sf = eq[t] / eq[t - 1], sf[t] / sf[t - 1]
        sleeves = [Sleeve(s.name, s.tradable, s.equity * r_eq, s.safe * r_sf) for s in sleeves]

        if t in decision_set:
            week += 1
            sig = _signals_at(t, dates, eq, signals, longest)
            new_state, decision = advance(state, sig, week, ladder)
            moved = abs(new_state.equity - state.equity) > _EPS
            state = new_state
            decisions.append((dates[t], decision))
            for k, s in enumerate(sleeves):
                if s.tradable and _needs_trade(s, state.equity, moved, p.drift_band):
                    sleeves[k], traded = _rebalance(s, state.equity, p.spread)
                    traded_total += traded

        total = sum(s.value for s in sleeves)
        index.append(index[-1] * (total / prev_total))
        values.append(total)
        advised.append(state.equity)

    return make_result(
        dates,
        index,
        values=values,
        advised_equity=advised,
        total_traded=traded_total,
        decisions=decisions,
        sleeves=sleeves,
        parameters=_parameters(p, signals, ladder),
        data_version=data_version,
        config_version=config_version,
        code_version=code_version,
    )


def run_fixed_weight(
    equity: Series,
    safe: Series,
    weight: float,
    *,
    schedule: Schedule,
    params: BacktestParams | None = None,
    data_version: str,
    config_version: str = "",
    label: str = "fixed_weight",
) -> BacktestResult:
    """A constant-weight two-asset portfolio.

    ``schedule`` is ``never`` (buy and hold), ``quarterly`` (rebalanced at the first close of
    each January, April, July and October) or ``decision_days`` (rebalanced on weekly decision
    days when the weight has drifted beyond the band). Spread applies to every rebalance.
    """
    p = params or BacktestParams()
    dates, eq, sf = align(equity, safe)
    sleeve = Sleeve(label, True, weight * p.initial_value, (1.0 - weight) * p.initial_value)
    decision_set = (
        set(decision_day_indices(dates, p.weekday)) if schedule == "decision_days" else set()
    )
    index, values, traded_total = [1.0], [sleeve.value], 0.0
    for t in range(1, len(dates)):
        prev = sleeve.value
        sleeve = Sleeve(
            sleeve.name, True, sleeve.equity * eq[t] / eq[t - 1], sleeve.safe * sf[t] / sf[t - 1]
        )
        due = False
        if schedule == "quarterly":
            d, last = dates[t], dates[t - 1]
            due = d.month in (1, 4, 7, 10) and (d.year, d.month) != (last.year, last.month)
        elif schedule == "decision_days" and t in decision_set:
            due = _needs_trade(sleeve, weight, False, p.drift_band)
        if due:
            sleeve, traded = _rebalance(sleeve, weight, p.spread)
            traded_total += traded
        index.append(index[-1] * sleeve.value / prev)
        values.append(sleeve.value)
    parameters = _parameters(p)
    parameters.update({"benchmark": label, "equity_weight": weight, "schedule": schedule})
    return make_result(
        dates,
        index,
        values=values,
        advised_equity=[weight] * len(dates),
        total_traded=traded_total,
        sleeves=[sleeve],
        parameters=parameters,
        data_version=data_version,
        config_version=config_version,
    )
