"""Fetch one series from an ordered list of providers, falling through on failure."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from guard.market.base import (
    Listing,
    MacroProvider,
    Observation,
    PriceProvider,
    ProviderError,
)

log = logging.getLogger("guard.market")


@dataclass(frozen=True)
class SourceSeries:
    source_id: str
    observations: tuple[Observation, ...]


@dataclass
class FetchResult:
    series_id: str
    sources: list[SourceSeries] = field(default_factory=list)  # providers that returned data
    errors: dict[str, str] = field(default_factory=dict)  # provider id -> failure text


def fetch_prices(
    listing: Listing,
    providers: Sequence[PriceProvider],
    start: date,
    end: date,
    *,
    max_sources: int = 2,
) -> FetchResult:
    """Ask providers in order until ``max_sources`` return data; failures are recorded."""
    result = FetchResult(listing.listing_id)
    for p in providers:
        if len(result.sources) >= max_sources:
            break
        if not p.supports(listing):
            continue
        try:
            obs = p.daily_closes(listing, start, end)
        except ProviderError as e:
            _record_failure(result, p.provider_id, str(e))
            continue
        _record(result, p.provider_id, obs)
    return result


def fetch_macro(
    series_id: str,
    providers: Sequence[MacroProvider],
    start: date,
    end: date,
    *,
    max_sources: int = 2,
) -> FetchResult:
    """Same fall-through for FX and macro series."""
    result = FetchResult(series_id)
    for p in providers:
        if len(result.sources) >= max_sources:
            break
        if not p.supports_series(series_id):
            continue
        try:
            obs = p.series(series_id, start, end)
        except ProviderError as e:
            _record_failure(result, p.provider_id, str(e))
            continue
        _record(result, p.provider_id, obs)
    return result


def _record(result: FetchResult, provider_id: str, obs: list[Observation]) -> None:
    if obs:
        result.sources.append(SourceSeries(provider_id, tuple(obs)))
    else:
        _record_failure(result, provider_id, "no data returned")


def _record_failure(result: FetchResult, provider_id: str, message: str) -> None:
    log.warning("provider %s gave no data for %s: %s", provider_id, result.series_id, message)
    result.errors[provider_id] = message
