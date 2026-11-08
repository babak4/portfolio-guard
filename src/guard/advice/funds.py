"""Fund reference data: what each fund is, what it costs and whether it counts as US-only."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Literal

from guard.ingest.model import is_fixed_satellite

Role = Literal["growth", "safe"]

_US_STOCK = re.compile(r"^([A-Z]+)_([A-Z])_US_EQ$")


@dataclass(frozen=True)
class Fund:
    """One line of the fund reference.

    ``ongoing_charge`` is a fraction (0.0019 is 0.19%); ``None`` means unknown and is never
    guessed. ``available_on_t212`` is ``None`` when unknown. ``index_group`` names the index the
    fund tracks; ``None`` means no like-for-like alternative is known.
    """

    symbol: str
    name: str
    role: Role
    category: str
    us_only: bool = False
    ongoing_charge: float | None = None
    available_on_t212: bool | None = None
    watchlist: bool = False
    verified: bool = False
    note: str = ""
    index_group: str | None = None


def symbol_of(ticker: str) -> str:
    """The fund symbol for a Trading212 ticker.

    ``VWRPl_EQ`` gives ``VWRP``: everything from the first underscore is dropped, then the
    trailing lowercase exchange letter. A US stock written ``BRK_B_US_EQ`` gives ``BRK.B``.
    """
    stock = _US_STOCK.match(ticker)
    if stock:
        return f"{stock.group(1)}.{stock.group(2)}"
    core = ticker.split("_")[0]
    return re.sub(r"[a-z]$", "", core)


class FundRegistry:
    """Funds by symbol, with ticker lookup and cheaper-alternative search."""

    def __init__(self, funds: Mapping[str, Fund]) -> None:
        self.by_symbol: dict[str, Fund] = dict(funds)

    def __iter__(self) -> Iterator[Fund]:
        return iter(self.by_symbol.values())

    def __len__(self) -> int:
        return len(self.by_symbol)

    def get(self, symbol: str) -> Fund | None:
        return self.by_symbol.get(symbol)

    def find(self, ticker: str) -> Fund | None:
        """The fund for a Trading212 ticker, matching symbols the way fixed satellites are."""
        symbol = symbol_of(ticker)
        exact = self.by_symbol.get(symbol)
        if exact is not None:
            return exact
        for fund in self.by_symbol.values():
            if is_fixed_satellite(symbol, None, [fund.symbol]):
                return fund
        return None

    def symbol_for(self, ticker: str) -> str:
        """The registry symbol when the ticker is known, otherwise the derived symbol."""
        fund = self.find(ticker)
        return fund.symbol if fund is not None else symbol_of(ticker)

    def alternatives(self, fund: Fund) -> tuple[Fund, ...]:
        """Like-for-like funds that cost strictly less, cheapest first.

        An alternative has the same role and the same non-null ``index_group``, and both
        ongoing charges must be known. Availability is the caller's concern.
        """
        if fund.ongoing_charge is None or fund.index_group is None:
            return ()
        cheaper = [
            f
            for f in self.by_symbol.values()
            if f.symbol != fund.symbol
            and f.role == fund.role
            and f.index_group == fund.index_group
            and f.ongoing_charge is not None
            and f.ongoing_charge < fund.ongoing_charge
        ]
        return tuple(sorted(cheaper, key=lambda f: (f.ongoing_charge or 0.0, f.symbol)))
