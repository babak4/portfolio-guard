"""Market data layer: swappable providers, cross-source verification and sanity checks.

Flow for one series::

    result  = fetch_prices(listing, [YahooProvider(), StooqProvider()], start, end)
    # or fetch_macro("USD_PER_GBP", [FredProvider(), FrankfurterProvider()], start, end,
    #                 aliases=DEFAULT_MACRO_ALIASES)
    checked = cross_check(result, rules)   # raises if not two independent, agreeing sources
    check_jumps(checked.series_id, checked.sources[0], checked.observations, rules)
    check_staleness(checked.series_id, checked.sources[0], checked.observations[-1].day,
                    as_of, rules, markets=("uk",))
    MarketStore(open_warehouse()).save(checked)

Types (``guard.market.base``)
    ``Observation(day, value: Decimal, filled=False)``; ``filled`` marks a carried-forward value.
    ``Listing(listing_id, ccy, yahoo_symbol, stooq_symbol, quoted_in_minor_units)`` with
    ``Listing.from_listing_id("VWRP:XLON", "GBP")``.
    ``PriceProvider``: ``provider_id``, ``supports(listing)``,
    ``daily_closes(listing, start, end)``.
    ``MacroProvider``: ``provider_id``, ``supports_series(id)``, ``series(id, start, end)``.
    Both return ``list[Observation]`` ordered by date and raise ``ProviderError`` on failure.
    ``RawSink = Callable[[source_id, name, bytes], None]``, passed to a provider's constructor.

Providers (``guard.market.providers``): ``YahooProvider(downloader, sink)`` (lazy yfinance),
``StooqProvider(client, sink)``, ``FrankfurterProvider(client, sink)`` (series ``GBPUSD`` style),
``FredProvider(client, sink)`` (``DEXUSUK``, ``VIXCLS``; holiday gaps forward-filled and flagged),
``MsciFileProvider(path, sink)`` (series ``MSCI_ACWI_NR_USD`` from a user file, ``GUARD_MSCI_FILE``
or ``<data dir>/inputs/msci_acwi_nr.csv``; raises ``ProviderUnavailable`` when absent).
Pass an ``httpx.Client`` with a mock transport to run any of them offline.

Fetching (``guard.market.fetch``): ``fetch_prices`` and ``fetch_macro`` try providers in order,
record failures (HTTP 429, timeouts, empty data) in ``FetchResult.errors`` and move on, stopping
after ``max_sources`` (default 2) return data. Malformed payloads (short rows, bad dates, non-UTF-8
bodies) count as provider failures. ``FetchResult(series_id, sources, errors)``. Providers name
FX/macro series differently, so ``fetch_macro(..., aliases=...)`` maps a canonical id to each
provider's own (``DEFAULT_MACRO_ALIASES``: ``USD_PER_GBP`` is FRED ``DEXUSUK`` and Frankfurter
``GBPUSD``, both US dollars per pound); the result keeps the canonical id.

Verification (all raise ``DataIntegrityError`` subclasses carrying ``rule``, ``series_id`` and
``sources``): ``cross_check(result, rules) -> CheckedSeries`` (one source or the same source twice:
``UntrustedSeriesError``; disagreement beyond ``crosscheck_tolerance``, a latest primary date
missing from the second source, or coverage below ``crosscheck_min_overlap``:
``CrossCheckError`` naming both; carried-forward ``filled`` observations are ignored),
``check_staleness`` (``StaleDataError``) and ``check_jumps`` (``JumpError``) in
``guard.market.sanity``. A ``CheckedSeries`` holds the
primary source's observations and every agreeing source id. Two sources are independent when their
provider ids differ; callers must pass providers with different upstreams.

Calendar (``guard.market.calendar``): ``is_trading_day`` and ``last_trading_day_before`` over
the ``"uk"`` and ``"us"`` markets (a day counts when all listed markets are open).

Storage: ``MarketStore(conn)`` keeps raw payloads (sha256 keyed) via ``raw_sink`` and cleaned
series via ``save`` and ``load``; all writes are idempotent. DuckDB access lives in
``guard.storage.market_store``.
"""

from guard.market.base import (
    CrossCheckError,
    DataIntegrityError,
    JumpError,
    Listing,
    MacroProvider,
    Observation,
    PriceProvider,
    ProviderError,
    ProviderUnavailable,
    RawSink,
    StaleDataError,
    UntrustedSeriesError,
)
from guard.market.crosscheck import CheckedSeries, cross_check
from guard.market.fetch import (
    DEFAULT_MACRO_ALIASES,
    FetchResult,
    SourceSeries,
    fetch_macro,
    fetch_prices,
)
from guard.market.sanity import check_jumps, check_staleness
from guard.market.store import MarketStore

__all__ = [
    "DEFAULT_MACRO_ALIASES",
    "CheckedSeries",
    "CrossCheckError",
    "DataIntegrityError",
    "FetchResult",
    "JumpError",
    "Listing",
    "MacroProvider",
    "MarketStore",
    "Observation",
    "PriceProvider",
    "ProviderError",
    "ProviderUnavailable",
    "RawSink",
    "SourceSeries",
    "StaleDataError",
    "UntrustedSeriesError",
    "check_jumps",
    "check_staleness",
    "cross_check",
    "fetch_macro",
    "fetch_prices",
]
