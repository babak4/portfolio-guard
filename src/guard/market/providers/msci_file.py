"""MSCI index history from a file the owner downloads by hand (personal use, never committed).

The file path comes from ``GUARD_MSCI_FILE`` or defaults to ``<data dir>/inputs/msci_acwi_nr.csv``
(``.xlsx`` is tried too). The first column holds dates, the second the index level. Rows above the
``Date`` header row and non-numeric footer rows are ignored.
"""

from __future__ import annotations

import csv
import io
import os
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from guard.market.base import Observation, ProviderUnavailable, RawSink
from guard.paths import data_dir

SOURCE_ID = "msci_file"
SERIES_ID = "MSCI_ACWI_NR_USD"
ENV_VAR = "GUARD_MSCI_FILE"


def resolve_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env).expanduser()
    base = data_dir() / "inputs"
    for name in ("msci_acwi_nr.csv", "msci_acwi_nr.xlsx"):
        if (base / name).exists():
            return base / name
    return base / "msci_acwi_nr.csv"


def parse_rows(table: list[list[str]]) -> list[Observation]:
    """Find the ``Date`` header row, then read (date, level) pairs below it."""
    header = next(
        (i for i, r in enumerate(table) if r and str(r[0]).strip().lower() == "date"), None
    )
    if header is None:
        raise ProviderUnavailable("msci_file: no 'Date' header row found")
    rows: list[Observation] = []
    for r in table[header + 1 :]:
        if len(r) < 2:
            continue
        try:
            day = pd.to_datetime(r[0]).date()
            value = Decimal(str(r[1]).replace(",", "").strip())
        except (ValueError, TypeError, InvalidOperation):
            continue
        if value.is_finite():
            rows.append(Observation(day, value))
    if not rows:
        raise ProviderUnavailable("msci_file: no numeric rows found")
    return sorted(rows, key=lambda o: o.day)


class MsciFileProvider:
    provider_id = SOURCE_ID

    def __init__(self, path: Path | None = None, sink: RawSink | None = None) -> None:
        self._path = path
        self._sink = sink

    def supports_series(self, series_id: str) -> bool:
        return series_id == SERIES_ID

    def series(self, series_id: str, start: date, end: date) -> list[Observation]:
        path = resolve_path(self._path)
        if not path.is_file():
            raise ProviderUnavailable(
                f"MSCI file not found at {path}. Download the ACWI net total return history "
                f"from MSCI and place it there, or set {ENV_VAR}."
            )
        payload = path.read_bytes()
        if self._sink is not None:
            self._sink(SOURCE_ID, path.name, payload)
        if path.suffix.lower() in (".xlsx", ".xls"):
            try:
                frame = pd.read_excel(io.BytesIO(payload), header=None, dtype=str)
            except ImportError as e:
                raise ProviderUnavailable(
                    "msci_file: reading Excel needs openpyxl; save the file as CSV instead"
                ) from e
            table = [[str(c) for c in row] for row in frame.fillna("").values.tolist()]
        else:
            table = list(csv.reader(io.StringIO(payload.decode("utf-8-sig"))))
        return [o for o in parse_rows(table) if start <= o.day <= end]
