"""Fetch one series from an ordered list of providers, falling through on failure."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import InvalidOperation
from typing import Any

from guard.market.base import (
    Listing,
    MacroProvider,
    Observation,
    PriceProvider,
    ProviderError,
)

log = logging.getLogger("guard.market")

# Canonical series id -> {provider id: the id that provider understands}. USD_PER_GBP is US
# dollars per one pound: FRED's DEXUSUK and Frankfurter's GBPUSD (base GBP, quote USD) match.
DEFAULT_MACRO_ALIASES: Mapping[str, Mapping[str, str]] = {
    "USD_PER_GBP": {"fred": "DEXUSUK", "frankfurter": "GBPUSD"},
}

# What a parser can raise on a short row, a missing key, a wrong type or a non-UTF-8 body.
_MALFORMED = (ValueError, TypeError, AttributeError, KeyError, IndexError, InvalidOperation)


@dataclass(frozen=True)
class SourceSeries:
    source_id: str
    observations: tuple[Observation, ...]


@dataclass
class FetchResult:
    series_id: str
    sources: list[SourceSeries] = field(default_factory=list)  # providers that returned data
    errors: dict[str, str] = field(default_factory=dict)  # provider id -> failure text


def _fetch(
    series_id: str,
    providers: Sequence[Any],
    supports: Callable[[Any], bool],
    load: Callable[[Any], list[Observation]],
    max_sources: int,
) -> FetchResult:
    result = FetchResult(series_id)
    for p in providers:
        if len(result.sources) >= max_sources:
            break
        if not supports(p):
            continue
        try:
            obs = load(p)
        except ProviderError as e:
            _record_failure(result, p.provider_id, str(e))
            continue
        except _MALFORMED as e:
            _record_failure(
                result, p.provider_id, f"{p.provider_id}: malformed data ({type(e).__name__})"
            )
            continue
        _record(result, p.provider_id, obs)
    return result


def fetch_prices(
    listing: Listing,
    providers: Sequence[PriceProvider],
    start: date,
    end: date,
    *,
    max_sources: int = 2,
) -> FetchResult:
    """Ask providers in order until ``max_sources`` return data; failures are recorded."""
    return _fetch(
        listing.listing_id,
        providers,
        lambda p: p.supports(listing),
        lambda p: p.daily_closes(listing, start, end),
        max_sources,
    )


def fetch_macro(
    series_id: str,
    providers: Sequence[MacroProvider],
    start: date,
    end: date,
    *,
    max_sources: int = 2,
    aliases: Mapping[str, Mapping[str, str]] | None = None,
) -> FetchResult:
    """Same fall-through for FX and macro series.

    ``aliases[series_id][provider_id]`` is the id that provider uses for the series (see
    ``DEFAULT_MACRO_ALIASES``); the result keeps the canonical ``series_id``.
    """
    names = (aliases or {}).get(series_id, {})

    def own(p: MacroProvider) -> str:
        return names.get(p.provider_id, series_id)

    return _fetch(
        series_id,
        providers,
        lambda p: p.supports_series(own(p)),
        lambda p: p.series(own(p), start, end),
        max_sources,
    )


def _record(result: FetchResult, provider_id: str, obs: list[Observation]) -> None:
    if obs:
        result.sources.append(SourceSeries(provider_id, tuple(obs)))
    else:
        _record_failure(result, provider_id, "no data returned")


def _record_failure(result: FetchResult, provider_id: str, message: str) -> None:
    log.warning("provider %s gave no data for %s: %s", provider_id, result.series_id, message)
    result.errors[provider_id] = message
