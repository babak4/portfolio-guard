"""Load the fund reference: packaged defaults plus an optional per-symbol override file."""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from guard.advice.funds import Fund, FundRegistry
from guard.paths import data_dir

_KEYS = {f for f in Fund.__dataclass_fields__}
_REQUIRED = {"symbol", "name", "role", "category"}


class FundFileError(ValueError):
    """The funds file is malformed."""


def _entries(text: str, source: str) -> list[dict[str, Any]]:
    loaded = yaml.safe_load(text) or {}
    entries = loaded.get("funds") if isinstance(loaded, dict) else None
    if not isinstance(entries, list):
        raise FundFileError(f"{source}: expected a 'funds' list")
    out: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or "symbol" not in entry:
            raise FundFileError(f"{source}: every fund needs a symbol")
        unknown = set(entry) - _KEYS
        if unknown:
            raise FundFileError(f"{source}: unknown keys for {entry['symbol']}: {sorted(unknown)}")
        out.append(entry)
    return out


def _build(merged: dict[str, dict[str, Any]]) -> FundRegistry:
    funds: dict[str, Fund] = {}
    for symbol, fields in merged.items():
        missing = _REQUIRED - set(fields)
        if missing:
            raise FundFileError(f"fund {symbol} is missing {sorted(missing)}")
        if fields["role"] not in ("growth", "safe"):
            raise FundFileError(f"fund {symbol}: role must be growth or safe")
        funds[symbol] = Fund(**fields)
    return FundRegistry(funds)


def _packaged_text() -> str:
    return resources.files("guard.config").joinpath("defaults/funds.yaml").read_text("utf-8")


def _merge(override_text: str | None, source: str) -> FundRegistry:
    merged: dict[str, dict[str, Any]] = {}
    for entry in _entries(_packaged_text(), "funds.yaml"):
        merged[entry["symbol"]] = dict(entry)
    if override_text is not None:
        for entry in _entries(override_text, source):
            merged.setdefault(entry["symbol"], {}).update(entry)
    return _build(merged)


def packaged_registry() -> FundRegistry:
    """The packaged defaults only."""
    return _merge(None, "")


def load_registry(override_path: Path | None = None) -> FundRegistry:
    """Packaged defaults merged per symbol with an override file.

    Without an explicit path, ``$GUARD_DATA_DIR/config/funds.yaml`` is used when it exists.
    """
    path = override_path
    if path is None and os.environ.get("GUARD_DATA_DIR"):
        candidate = data_dir() / "config" / "funds.yaml"
        path = candidate if candidate.exists() else None
    if path is None:
        return packaged_registry()
    return _merge(path.read_text("utf-8"), str(path))
