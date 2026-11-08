from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from guard.config.models import DataRules
from guard.market import JumpError, Observation, StaleDataError, check_jumps, check_staleness
from guard.market.calendar import is_trading_day, last_trading_day_before

RULES = DataRules(
    jump_threshold=0.15, crosscheck_tolerance=0.005, stale_trading_days=0, max_gap_days=10
)


def _obs(*values: str) -> list[Observation]:
    return [Observation(date(2026, 3, 2 + i), Decimal(v)) for i, v in enumerate(values)]


def test_25_percent_jump_stops_with_source_and_rule() -> None:
    with pytest.raises(JumpError) as exc:
        check_jumps("VWRP:XLON", "yahoo", _obs("100", "125"), RULES)
    assert exc.value.rule == "jump"
    assert exc.value.sources == ("yahoo",)


def test_14_percent_jump_passes_and_downward_jump_stops() -> None:
    check_jumps("S", "yahoo", _obs("100", "114"), RULES)
    with pytest.raises(JumpError):
        check_jumps("S", "yahoo", _obs("100", "80"), RULES)


# Monday 25 May 2026 is the UK spring bank holiday.
AS_OF_AFTER_HOLIDAY = date(2026, 5, 26)


def test_expected_last_close_skips_bank_holiday_and_weekend() -> None:
    assert last_trading_day_before(AS_OF_AFTER_HOLIDAY, ("uk",)) == date(2026, 5, 22)


def test_close_across_holiday_gap_is_fresh() -> None:
    # Friday close seen on Tuesday after the Monday holiday: 4 calendar days old, 0 trading days late.
    check_staleness("S", "stooq", date(2026, 5, 22), AS_OF_AFTER_HOLIDAY, RULES, ("uk",))


def test_close_older_than_expected_last_trading_day_is_stale() -> None:
    with pytest.raises(StaleDataError) as exc:
        check_staleness("S", "stooq", date(2026, 5, 21), AS_OF_AFTER_HOLIDAY, RULES, ("uk",))
    assert exc.value.rule == "staleness"
    assert exc.value.sources == ("stooq",)


def test_allowed_lag_is_configurable() -> None:
    lenient = RULES.model_copy(update={"stale_trading_days": 2})
    check_staleness("S", "stooq", date(2026, 5, 20), AS_OF_AFTER_HOLIDAY, lenient, ("uk",))
    with pytest.raises(StaleDataError):
        check_staleness("S", "stooq", date(2026, 5, 18), AS_OF_AFTER_HOLIDAY, lenient, ("uk",))


@pytest.mark.parametrize(
    ("day", "markets", "expected"),
    [
        (date(2026, 4, 3), ("uk",), False),  # Good Friday
        (date(2026, 4, 6), ("uk",), False),  # Easter Monday
        (date(2026, 4, 6), ("us",), True),
        (date(2026, 7, 3), ("us",), False),  # 4 July is a Saturday, observed Friday
        (date(2026, 11, 26), ("us",), False),  # Thanksgiving
        (date(2026, 11, 26), ("uk",), True),
        (date(2026, 12, 25), ("uk",), False),
        (date(2026, 12, 28), ("uk",), False),  # Boxing Day (Saturday) substitute
        (date(2026, 11, 26), ("uk", "us"), False),
        (date(2026, 5, 23), ("uk",), False),  # Saturday
    ],
)
def test_trading_day_calendar(day: date, markets: tuple[str, ...], expected: bool) -> None:
    assert is_trading_day(day, markets) is expected
