"""Shared builders for advice tests."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from importlib import resources
from typing import Any

import yaml

from guard.advice import AdviceInputs, FundRegistry
from guard.advice.fund_registry import packaged_registry
from guard.config.models import GuardConfig
from guard.ingest.model import Holding

TICKERS = {
    "VWRP": "VWRPl_EQ",
    "VUAG": "VUAGl_EQ",
    "CSH2": "CSH2l_EQ",
    "XSTR": "XSTRl_EQ",
    "BRK.B": "BRK_B_US_EQ",
    "CUKX": "CUKXl_EQ",
    "VMID": "VMIDl_EQ",
    "XDEW": "XDEWl_EQ",
    "VWCE": "VWCEd_EQ",
}


def make_config(**sections: dict[str, Any]) -> GuardConfig:
    text = resources.files("guard.config").joinpath("defaults/guard.yaml").read_text("utf-8")
    raw = yaml.safe_load(text)
    for name, values in sections.items():
        raw[name] = {**raw[name], **values}
    return GuardConfig.model_validate(raw)


def holding(account: str, symbol: str, value: int | str, *, excluded: bool = False) -> Holding:
    ticker = TICKERS.get(symbol, f"{symbol}l_EQ")
    return Holding(
        account,
        "trading212",
        f"ISIN-{symbol}",
        ticker,
        symbol,
        Decimal(1),
        Decimal(str(value)),
        Decimal(str(value)),
        excluded,
    )


def registry() -> FundRegistry:
    return packaged_registry()


def make_inputs(
    holdings: list[Holding],
    *,
    before: float = 0.9,
    after: float = 0.9,
    cash: Mapping[str, int | str] | None = None,
    contributions: Mapping[str, int | str] | None = None,
    config: GuardConfig | None = None,
    paused: bool = False,
    available: frozenset[str] | None = None,
    funds: FundRegistry | None = None,
) -> AdviceInputs:
    return AdviceInputs(
        holdings=tuple(holdings),
        cash={k: Decimal(str(v)) for k, v in (cash or {}).items()},
        equity_before=before,
        equity_after=after,
        contributions={k: Decimal(str(v)) for k, v in (contributions or {}).items()},
        funds=funds or registry(),
        config=config or make_config(),
        paused=paused,
        available_symbols=available,
    )
