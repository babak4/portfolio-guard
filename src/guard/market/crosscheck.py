"""A series is trusted only when two independent sources agree within the tolerance."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from guard.market.base import CrossCheckError, Observation, UntrustedSeriesError
from guard.market.fetch import FetchResult


@dataclass(frozen=True)
class CheckedSeries:
    """An accepted series: the primary source's observations plus every source that agreed."""

    series_id: str
    observations: tuple[Observation, ...]
    sources: tuple[str, ...]
    max_difference: Decimal  # worst relative difference seen on shared dates


def cross_check(result: FetchResult, tolerance: float) -> CheckedSeries:
    """Compare the first two sources on their shared dates.

    The first source's observations are kept. Raises ``UntrustedSeriesError`` with fewer than two
    sources and ``CrossCheckError`` (naming both) when they share no date or differ by more than
    ``tolerance`` (a fraction of the second source's value) on any shared date.
    """
    if len(result.sources) < 2:
        have = tuple(s.source_id for s in result.sources)
        raise UntrustedSeriesError(
            "single_source",
            result.series_id,
            have,
            f"needs two independent sources, got {len(have)}; errors: {result.errors}",
        )
    first, second = result.sources[0], result.sources[1]
    names = (first.source_id, second.source_id)
    other = {o.day: o.value for o in second.observations}
    worst = Decimal(0)
    shared = 0
    for obs in first.observations:
        ref = other.get(obs.day)
        if ref is None:
            continue
        shared += 1
        diff = abs(obs.value - ref) / abs(ref) if ref != 0 else Decimal("Infinity")
        worst = max(worst, diff)
        if diff > Decimal(str(tolerance)):
            raise CrossCheckError(
                "crosscheck",
                result.series_id,
                names,
                f"{obs.day}: {obs.value} vs {ref} differ by {float(diff):.2%}, "
                f"tolerance {tolerance:.2%}",
            )
    if shared == 0:
        raise CrossCheckError(
            "crosscheck", result.series_id, names, "the sources share no dates to compare"
        )
    return CheckedSeries(result.series_id, first.observations, names, worst)
