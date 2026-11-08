"""Holdings, snapshots and the combined portfolio view. Pure data, no I/O."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

ZERO = Decimal(0)


class IngestIntegrityError(RuntimeError):
    """Ingested data cannot be trusted; the run must stop rather than guess."""


@dataclass(frozen=True)
class Holding:
    """One line in one account.

    ``price`` is in the instrument currency in major units (never pence); ``value_gbp`` is pounds.
    """

    account_id: str
    platform: str
    isin: str
    ticker: str
    name: str
    quantity: Decimal
    price: Decimal
    value_gbp: Decimal
    excluded: bool


@dataclass(frozen=True)
class Snapshot:
    account_id: str
    platform: str
    day: date
    taken_at: datetime
    holdings: tuple[Holding, ...]
    cash: Decimal
    total: Decimal


@dataclass(frozen=True)
class PortfolioLine:
    isin: str
    name: str
    quantity: Decimal
    value_gbp: Decimal
    excluded: bool
    accounts: tuple[str, ...]


@dataclass(frozen=True)
class PortfolioView:
    lines: tuple[PortfolioLine, ...]
    cash: Decimal
    total: Decimal
    tactical_base: Decimal


def _symbol(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def is_fixed_satellite(ticker: str, short_name: str | None, satellites: Iterable[str]) -> bool:
    """True when the instrument matches a configured satellite symbol.

    Trading212 tickers look like ``BRKB_US_EQ``; the part before the first underscore is compared
    with the configured symbol, ignoring punctuation and case. ``short_name`` is also compared.
    """
    candidates = {_symbol(ticker.split("_")[0])}
    if short_name:
        candidates.add(_symbol(short_name))
    return any(_symbol(s) in candidates for s in satellites)


def portfolio_view(snapshots: Sequence[Snapshot]) -> PortfolioView:
    """Combine accounts: lines with the same ISIN merge across accounts and platforms."""
    merged: dict[str, PortfolioLine] = {}
    for snap in snapshots:
        for h in snap.holdings:
            prev = merged.get(h.isin)
            if prev is None:
                merged[h.isin] = PortfolioLine(
                    h.isin, h.name, h.quantity, h.value_gbp, h.excluded, (h.account_id,)
                )
            else:
                merged[h.isin] = PortfolioLine(
                    h.isin,
                    prev.name,
                    prev.quantity + h.quantity,
                    prev.value_gbp + h.value_gbp,
                    prev.excluded or h.excluded,
                    (*prev.accounts, h.account_id),
                )
    lines = tuple(sorted(merged.values(), key=lambda ln: ln.isin))
    cash = sum((s.cash for s in snapshots), ZERO)
    total = sum((s.total for s in snapshots), ZERO)
    tactical = sum((ln.value_gbp for ln in lines if not ln.excluded), ZERO)
    return PortfolioView(lines, cash, total, tactical)
