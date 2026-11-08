from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from guard.market import CheckedSeries, MarketStore, Observation
from guard.storage import open_warehouse
from guard.storage.market_store import raw_hashes


def test_raw_payload_hashing_is_idempotent(data_dir: Path) -> None:
    conn = open_warehouse()
    store = MarketStore(conn)
    store.raw_sink("stooq", "a.csv", b"payload")
    store.raw_sink("stooq", "a.csv", b"payload")
    store.raw_sink("stooq", "b.csv", b"other")
    hashes = raw_hashes(conn, "stooq")
    assert len(hashes) == 2
    import hashlib

    assert hashlib.sha256(b"payload").hexdigest() in hashes


def test_clean_series_save_is_idempotent_and_records_sources(data_dir: Path) -> None:
    conn = open_warehouse()
    store = MarketStore(conn)
    checked = CheckedSeries(
        "DEXUSUK",
        (
            Observation(date(2026, 5, 22), Decimal("1.3525")),
            Observation(date(2026, 5, 25), Decimal("1.3525"), filled=True),
        ),
        ("fred", "frankfurter"),
        Decimal(0),
    )
    store.save(checked)
    store.save(checked)
    assert store.load("DEXUSUK") == list(checked.observations)
    row = conn.execute("SELECT count(*), min(sources) FROM market_series").fetchone()
    assert row == (2, "fred+frankfurter")
