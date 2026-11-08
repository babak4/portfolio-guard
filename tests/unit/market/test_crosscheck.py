from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from guard.config.models import DataRules
from guard.market import (
    CrossCheckError,
    FetchResult,
    Observation,
    SourceSeries,
    UntrustedSeriesError,
    cross_check,
)

TOL = 0.005
RULES = DataRules(
    jump_threshold=0.15,
    crosscheck_tolerance=TOL,
    crosscheck_min_overlap=0.9,
    stale_trading_days=0,
    max_gap_days=10,
)


def _series(source: str, *values: str) -> SourceSeries:
    return SourceSeries(
        source, tuple(Observation(date(2026, 5, 18 + i), Decimal(v)) for i, v in enumerate(values))
    )


def test_agreement_within_tolerance_is_accepted_and_records_both_sources() -> None:
    result = FetchResult(
        "VWRP:XLON", [_series("yahoo", "100", "101"), _series("stooq", "100.2", "101")]
    )
    checked = cross_check(result, RULES)
    assert checked.sources == ("yahoo", "stooq")
    assert [o.value for o in checked.observations] == [Decimal("100"), Decimal("101")]
    assert checked.max_difference == Decimal("0.2") / Decimal("100.2")


def test_disagreement_beyond_tolerance_names_both_sources() -> None:
    result = FetchResult(
        "VWRP:XLON", [_series("yahoo", "100", "101"), _series("stooq", "100", "102")]
    )
    with pytest.raises(CrossCheckError) as exc:
        cross_check(result, RULES)
    assert exc.value.rule == "crosscheck"
    assert exc.value.sources == ("yahoo", "stooq")
    assert "yahoo" in str(exc.value) and "stooq" in str(exc.value)


def test_single_source_is_untrusted_and_stops() -> None:
    result = FetchResult(
        "VWRP:XLON", [_series("yahoo", "100")], errors={"stooq": "stooq: HTTP 429"}
    )
    with pytest.raises(UntrustedSeriesError) as exc:
        cross_check(result, RULES)
    assert exc.value.rule == "single_source"
    assert exc.value.sources == ("yahoo",)


def test_no_sources_is_untrusted() -> None:
    with pytest.raises(UntrustedSeriesError):
        cross_check(FetchResult("X"), RULES)


def test_sources_with_no_shared_dates_cannot_be_compared() -> None:
    other = SourceSeries("stooq", (Observation(date(2026, 1, 1), Decimal("100")),))
    with pytest.raises(CrossCheckError, match="latest date"):
        cross_check(FetchResult("X", [_series("yahoo", "100"), other]), RULES)


def _dated(source: str, start_day: int, *values: str, filled: tuple[int, ...] = ()) -> SourceSeries:
    return SourceSeries(
        source,
        tuple(
            Observation(date(2026, 1, start_day + i), Decimal(v), filled=(start_day + i) in filled)
            for i, v in enumerate(values)
        ),
    )


def test_a_single_shared_date_is_not_enough() -> None:
    primary = _dated("yahoo", 1, *["100"] * 10)
    second = _dated("stooq", 10, "100")  # shares only the last day
    with pytest.raises(CrossCheckError, match="cover") as exc:
        cross_check(FetchResult("X", [primary, second]), RULES)
    assert exc.value.sources == ("yahoo", "stooq")


def test_latest_primary_date_must_be_present_in_second_source() -> None:
    primary = _dated("yahoo", 1, *["100"] * 10)
    second = _dated("stooq", 1, *["100"] * 9)  # 90% overlap but misses the latest day
    with pytest.raises(CrossCheckError, match="latest") as exc:
        cross_check(FetchResult("X", [primary, second]), RULES)
    assert exc.value.sources == ("yahoo", "stooq")


def test_overlap_exactly_at_minimum_passes_and_below_fails() -> None:
    primary = _dated("yahoo", 1, *["100"] * 10)
    nine = SourceSeries("stooq", primary.observations[1:])  # 9 of 10 dates, has latest
    assert cross_check(FetchResult("X", [primary, nine]), RULES).sources == ("yahoo", "stooq")
    eight = SourceSeries("stooq", primary.observations[2:])
    with pytest.raises(CrossCheckError, match="cover"):
        cross_check(FetchResult("X", [primary, eight]), RULES)


def test_same_source_twice_is_not_independent() -> None:
    a = _dated("yahoo", 1, "100", "100")
    with pytest.raises(UntrustedSeriesError) as exc:
        cross_check(FetchResult("X", [a, a]), RULES)
    assert exc.value.sources == ("yahoo", "yahoo")


def test_filled_observations_are_ignored_on_either_side() -> None:
    # Primary's day 5 is carried forward and wildly different in the second: not compared.
    primary = _dated(
        "fred", 1, "100", "100", "100", "100", "100", "100", "100", "100", "100", "100", filled=(5,)
    )
    second = _dated(
        "frankfurter", 1, "100", "100", "100", "100", "999", "100", "100", "100", "100", "100"
    )
    assert cross_check(FetchResult("X", [primary, second]), RULES).sources == (
        "fred",
        "frankfurter",
    )
    # A filled value in the second source does not count as covering that date.
    second_filled = _dated("frankfurter", 1, *["100"] * 10, filled=(1, 2))
    with pytest.raises(CrossCheckError, match="cover"):
        cross_check(FetchResult("X", [primary, second_filled]), RULES)


def test_latest_primary_value_filled_is_not_agreement() -> None:
    primary = _dated("fred", 1, *["100"] * 10, filled=(10,))
    second = _dated("frankfurter", 1, *["100"] * 10)
    # The latest real observation is day 9, which the second source has: still accepted.
    assert cross_check(FetchResult("X", [primary, second]), RULES).sources[0] == "fred"


def test_difference_exactly_at_tolerance_passes() -> None:
    primary = _dated("yahoo", 1, "100.5")
    second = _dated("stooq", 1, "100")  # 0.5 / 100 == 0.005
    assert cross_check(FetchResult("X", [primary, second]), RULES).max_difference == Decimal(
        "0.005"
    )
    just_over = _dated("yahoo", 1, "100.51")
    with pytest.raises(CrossCheckError):
        cross_check(FetchResult("X", [just_over, second]), RULES)


def test_zero_reference_value_does_not_divide_by_zero() -> None:
    zero = _dated("stooq", 1, "0")
    with pytest.raises(CrossCheckError):
        cross_check(FetchResult("X", [_dated("yahoo", 1, "1"), zero]), RULES)
    both = cross_check(FetchResult("X", [_dated("yahoo", 1, "0"), zero]), RULES)
    assert both.max_difference == Decimal(0)


def test_dates_present_in_only_one_source_are_not_compared() -> None:
    primary = _dated("yahoo", 1, *["100"] * 20)
    extra = (*primary.observations[1:], Observation(date(2026, 2, 1), Decimal("5")))
    second = SourceSeries("stooq", extra)  # 19/20 overlap; its extra date is ignored
    assert cross_check(FetchResult("X", [primary, second]), RULES).max_difference == 0
    bad_obs = [Observation(o.day, Decimal("150") if o.day.day == 5 else o.value) for o in extra]
    bad = SourceSeries("stooq", tuple(bad_obs))
    with pytest.raises(CrossCheckError, match="2026-01-05"):
        cross_check(FetchResult("X", [primary, bad]), RULES)
