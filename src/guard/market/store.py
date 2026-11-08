"""Warehouse-backed storage for raw payloads and cleaned series."""

from __future__ import annotations

from guard.market.base import Observation
from guard.market.crosscheck import CheckedSeries
from guard.storage import market_store
from guard.storage.market_store import Connection


class MarketStore:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def raw_sink(self, source_id: str, name: str, payload: bytes) -> None:
        """A ``RawSink``: store the payload hashed with sha256; repeats are ignored."""
        market_store.record_raw(self._conn, source_id, name, payload)

    def save(self, checked: CheckedSeries) -> None:
        """Store an accepted series with the sources that agreed on it."""
        market_store.save_clean_series(
            self._conn,
            checked.series_id,
            [(o.day, o.value, o.filled) for o in checked.observations],
            "+".join(checked.sources),
        )

    def load(self, series_id: str) -> list[Observation]:
        return [
            Observation(d, v, filled)
            for d, v, filled, _ in market_store.load_clean_series(self._conn, series_id)
        ]
