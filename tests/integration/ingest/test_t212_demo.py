"""Manual check against the Trading212 demo host with a read-only key.

Needs secrets ``guard_t212_key_demo`` and ``guard_t212_secret_demo`` (key created with trading
scopes disabled). Run with ``uv run pytest -m live tests/integration/ingest``.
"""

from __future__ import annotations

import pytest

from guard.ingest.t212 import DEMO_BASE_URL, INSTRUMENTS, POSITIONS, SUMMARY, client_for_account

pytestmark = pytest.mark.live


@pytest.mark.skip(reason="live: run explicitly with -m live and a demo read-only key")
def test_demo_positions_and_cash_are_readable() -> None:
    client = client_for_account("demo", DEMO_BASE_URL)
    try:
        positions = client.get_all(POSITIONS)
        summary = client.get(SUMMARY)
        instruments = client.get_all(INSTRUMENTS)
    finally:
        client.close()
    assert isinstance(positions, list)
    assert "totalValue" in summary and "cash" in summary
    assert instruments
