"""Load the history source chains and assemble a chain from whatever sources are available."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from importlib import resources
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from guard.market.base import Observation, ProviderUnavailable
from guard.market.history import Currency, Kind, Provenance, SeriesInput


class ChainEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    provenance: Provenance
    currency: Currency
    kind: Kind
    fee: float


def load_chains() -> dict[str, tuple[ChainEntry, ...]]:
    text = resources.files("guard.config").joinpath("defaults/history_sources.yaml").read_text()
    raw: dict[str, Any] = yaml.safe_load(text)
    return {
        asset: tuple(ChainEntry.model_validate(e) for e in entries)
        for asset, entries in raw["assets"].items()
    }


def assemble(
    asset: str,
    loader: Callable[[str], Sequence[Observation]],
    chains: dict[str, tuple[ChainEntry, ...]] | None = None,
) -> tuple[list[SeriesInput], dict[str, str]]:
    """Return the available chain for ``asset`` and the reasons the others were skipped.

    A source whose loader raises ``ProviderUnavailable`` (for example an absent MSCI file) is
    skipped, so the chain falls through to the next stand-in; its entries stay tagged proxy.
    """
    entries = (chains or load_chains())[asset]
    available: list[SeriesInput] = []
    skipped: dict[str, str] = {}
    for e in entries:
        try:
            obs = loader(e.source)
        except ProviderUnavailable as err:
            skipped[e.source] = str(err)
            continue
        available.append(SeriesInput(e.source, e.provenance, obs, e.currency, e.kind, e.fee))
    return available, skipped
