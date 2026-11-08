"""Staleness and jump checks. Both raise a run-stopping ``DataIntegrityError``."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from guard.config.models import DataRules
from guard.market.base import JumpError, Observation, StaleDataError
from guard.market.calendar import is_trading_day, last_trading_day_before


def check_staleness(
    series_id: str,
    source_id: str,
    last_close: date,
    as_of: date,
    rules: DataRules,
    markets: Sequence[str] = ("uk", "us"),
) -> None:
    """Stop when ``last_close`` trails the latest completed trading day by more than
    ``rules.stale_trading_days`` trading days. Holidays and weekends do not count as lag.
    """
    expected = last_trading_day_before(as_of, markets)
    lag = 0
    d = last_close + timedelta(days=1)
    while d <= expected:
        if is_trading_day(d, markets):
            lag += 1
        d += timedelta(days=1)
    if lag > rules.stale_trading_days:
        raise StaleDataError(
            "staleness",
            series_id,
            (source_id,),
            f"last close {last_close} is {lag} trading days behind expected {expected} "
            f"(allowed {rules.stale_trading_days})",
        )


def check_jumps(
    series_id: str, source_id: str, observations: Sequence[Observation], rules: DataRules
) -> None:
    """Stop when a consecutive pair of observations moves by more than the jump threshold."""
    for prev, cur in zip(observations, observations[1:], strict=False):
        if prev.value == 0:
            continue
        move = abs(cur.value / prev.value - 1)
        if move > rules.jump_threshold:
            raise JumpError(
                "jump",
                series_id,
                (source_id,),
                f"{prev.day} to {cur.day} moved {float(move):.1%}, "
                f"above the {rules.jump_threshold:.1%} threshold",
            )
