"""A series is trusted only when two independent sources agree within the tolerance."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from guard.config.models import DataRules
from guard.market.base import CrossCheckError, Observation, UntrustedSeriesError
from guard.market.fetch import FetchResult


@dataclass(frozen=True)
class CheckedSeries:
    """An accepted series: the primary source's observations plus every source that agreed."""

    series_id: str
    observations: tuple[Observation, ...]
    sources: tuple[str, ...]
    max_difference: Decimal  # worst relative difference seen on shared dates


def cross_check(result: FetchResult, rules: DataRules) -> CheckedSeries:
    """Compare the first two sources on their real (not carried-forward) observations.

    The first source's observations are kept. Raises ``UntrustedSeriesError`` with fewer than two
    sources or when both are the same source, and ``CrossCheckError`` (naming both) when:

    - the primary's latest real date is missing from the second source;
    - the second source covers less than ``rules.crosscheck_min_overlap`` of the primary's dates;
    - any shared date differs by more than ``rules.crosscheck_tolerance`` (a fraction of the
      second source's value; a zero reference only agrees with zero).

    Observations flagged ``filled`` on either side are ignored.
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
    if first.source_id == second.source_id:
        raise UntrustedSeriesError(
            "single_source",
            result.series_id,
            names,
            f"both results come from {first.source_id!r}; sources must be independent",
        )

    def fail(message: str) -> CrossCheckError:
        return CrossCheckError("crosscheck", result.series_id, names, message)

    tolerance = Decimal(str(rules.crosscheck_tolerance))
    primary = [o for o in first.observations if not o.filled]
    other = {o.day: o.value for o in second.observations if not o.filled}
    if not primary:
        raise fail("the primary source has no real observations to compare")
    latest = primary[-1]
    if latest.day not in other:
        raise fail(f"the second source has no value for the latest date {latest.day}")
    covered = sum(1 for o in primary if o.day in other)
    coverage = covered / len(primary)
    if coverage < rules.crosscheck_min_overlap:
        raise fail(
            f"the second source covers {covered} of {len(primary)} dates ({coverage:.1%}), "
            f"below the required {rules.crosscheck_min_overlap:.1%}"
        )
    worst = Decimal(0)
    for obs in primary:
        ref = other.get(obs.day)
        if ref is None:
            continue
        if ref == 0:
            diff = Decimal(0) if obs.value == 0 else Decimal("Infinity")
        else:
            diff = abs(obs.value - ref) / abs(ref)
        worst = max(worst, diff)
        if diff > tolerance:
            raise fail(
                f"{obs.day}: {obs.value} vs {ref} differ by {float(diff):.2%}, "
                f"tolerance {rules.crosscheck_tolerance:.2%}"
            )
    return CheckedSeries(result.series_id, first.observations, names, worst)
