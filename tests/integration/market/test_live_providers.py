"""Manual checks against the real services: `uv run pytest tests/integration/market -m live`.

Network is blocked by default (pytest-socket), so these also need `--force-enable-socket`.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from guard.market import Listing
from guard.market.providers.frankfurter import FrankfurterProvider
from guard.market.providers.fred import FredProvider
from guard.market.providers.stooq import StooqProvider
from guard.market.providers.yahoo import YahooProvider

pytestmark = pytest.mark.live

END = date.today()
START = END - timedelta(days=14)
VWRP = Listing.from_listing_id("VWRP:XLON", "GBP")


def test_yahoo_returns_recent_close() -> None:
    assert YahooProvider().daily_closes(VWRP, START, END)


def test_stooq_returns_recent_close() -> None:
    assert StooqProvider().daily_closes(VWRP, START, END)


def test_frankfurter_returns_recent_rate() -> None:
    assert FrankfurterProvider().series("GBPUSD", START, END)


def test_fred_returns_recent_rate() -> None:
    assert FredProvider().series("DEXUSUK", START, END)
