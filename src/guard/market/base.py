"""Shared types: observations, listings, provider protocols and the error hierarchy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable

# (source_id, payload name, payload bytes): receives every raw response before it is parsed.
RawSink = Callable[[str, str, bytes], None]


class ProviderError(RuntimeError):
    """A provider failed (HTTP error, bad payload). The fetch layer falls through to the next."""


class ProviderUnavailable(ProviderError):
    """The provider cannot run here (optional dependency or user file missing)."""


class DataIntegrityError(RuntimeError):
    """A data rule failed. The run must stop and alert; nothing is guessed.

    ``rule`` names the failed rule, ``sources`` the source ids involved (two for a mismatch).
    """

    def __init__(self, rule: str, series_id: str, sources: tuple[str, ...], message: str) -> None:
        super().__init__(f"[{rule}] {series_id} ({', '.join(sources) or 'no source'}): {message}")
        self.rule = rule
        self.series_id = series_id
        self.sources = sources


class UntrustedSeriesError(DataIntegrityError):
    """Fewer than two independent sources returned data."""


class CrossCheckError(DataIntegrityError):
    """Two sources disagree beyond the tolerance, or share no dates to compare."""


class StaleDataError(DataIntegrityError):
    """The latest close is older than the expected last trading day allows."""


class JumpError(DataIntegrityError):
    """A one-day move exceeds the configured threshold."""


@dataclass(frozen=True)
class Observation:
    """One dated value. ``filled`` marks a value carried forward from the previous day."""

    day: date
    value: Decimal
    filled: bool = False


_MIC_SUFFIX: dict[str, tuple[str, str]] = {
    "XLON": (".L", ".uk"),
    "XNYS": ("", ".us"),
    "XNAS": ("", ".us"),
    "XETR": (".DE", ".de"),
    "XAMS": (".AS", ".nl"),
}


@dataclass(frozen=True)
class Listing:
    """A tradeable line. ``listing_id`` is ``<ticker>:<mic>``, e.g. ``VWRP:XLON``."""

    listing_id: str
    ccy: str
    yahoo_symbol: str | None = None
    stooq_symbol: str | None = None
    # True when the source quotes in pence although ``ccy`` is the major unit (providers divide).
    quoted_in_minor_units: bool = False

    @classmethod
    def from_listing_id(
        cls, listing_id: str, ccy: str, *, quoted_in_minor_units: bool = False
    ) -> Listing:
        """Build a listing, deriving Yahoo and Stooq symbols from the MIC where known."""
        ticker, sep, mic = listing_id.partition(":")
        if not sep or not ticker or not mic:
            raise ValueError(f"listing_id must be '<ticker>:<mic>': {listing_id!r}")
        suffixes = _MIC_SUFFIX.get(mic)
        if suffixes is None:
            return cls(listing_id, ccy, None, None, quoted_in_minor_units)
        ysuf, ssuf = suffixes
        base = ticker.upper().replace(".", "-") if mic in ("XNYS", "XNAS") else ticker.upper()
        return cls(listing_id, ccy, base + ysuf, base.lower() + ssuf, quoted_in_minor_units)


@runtime_checkable
class PriceProvider(Protocol):
    provider_id: str

    def supports(self, listing: Listing) -> bool: ...

    def daily_closes(self, listing: Listing, start: date, end: date) -> list[Observation]:
        """Closes in ``[start, end]`` ordered by date. Raises ``ProviderError`` on failure."""
        ...


@runtime_checkable
class MacroProvider(Protocol):
    provider_id: str

    def supports_series(self, series_id: str) -> bool: ...

    def series(self, series_id: str, start: date, end: date) -> list[Observation]:
        """Observations in ``[start, end]`` ordered by date. Raises ``ProviderError`` on failure."""
        ...
