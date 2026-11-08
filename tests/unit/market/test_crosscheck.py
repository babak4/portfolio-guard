from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from guard.market import (
    CrossCheckError,
    FetchResult,
    Observation,
    SourceSeries,
    UntrustedSeriesError,
    cross_check,
)

TOL = 0.005


def _series(source: str, *values: str) -> SourceSeries:
    return SourceSeries(
        source, tuple(Observation(date(2026, 5, 18 + i), Decimal(v)) for i, v in enumerate(values))
    )


def test_agreement_within_tolerance_is_accepted_and_records_both_sources() -> None:
    result = FetchResult(
        "VWRP:XLON", [_series("yahoo", "100", "101"), _series("stooq", "100.2", "101")]
    )
    checked = cross_check(result, TOL)
    assert checked.sources == ("yahoo", "stooq")
    assert [o.value for o in checked.observations] == [Decimal("100"), Decimal("101")]
    assert checked.max_difference == Decimal("0.2") / Decimal("100.2")


def test_disagreement_beyond_tolerance_names_both_sources() -> None:
    result = FetchResult(
        "VWRP:XLON", [_series("yahoo", "100", "101"), _series("stooq", "100", "102")]
    )
    with pytest.raises(CrossCheckError) as exc:
        cross_check(result, TOL)
    assert exc.value.rule == "crosscheck"
    assert exc.value.sources == ("yahoo", "stooq")
    assert "yahoo" in str(exc.value) and "stooq" in str(exc.value)


def test_single_source_is_untrusted_and_stops() -> None:
    result = FetchResult(
        "VWRP:XLON", [_series("yahoo", "100")], errors={"stooq": "stooq: HTTP 429"}
    )
    with pytest.raises(UntrustedSeriesError) as exc:
        cross_check(result, TOL)
    assert exc.value.rule == "single_source"
    assert exc.value.sources == ("yahoo",)


def test_no_sources_is_untrusted() -> None:
    with pytest.raises(UntrustedSeriesError):
        cross_check(FetchResult("X"), TOL)


def test_sources_with_no_shared_dates_cannot_be_compared() -> None:
    other = SourceSeries("stooq", (Observation(date(2026, 1, 1), Decimal("100")),))
    with pytest.raises(CrossCheckError, match="share no dates"):
        cross_check(FetchResult("X", [_series("yahoo", "100"), other]), TOL)
