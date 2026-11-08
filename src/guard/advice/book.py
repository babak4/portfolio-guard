"""The tactical book: value per (account, fund symbol) for non-excluded, known funds.

Fixed satellites and instruments missing from the fund reference are not part of it, so they
are never traded and never counted in the tactical totals.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal

from guard.advice.funds import FundRegistry
from guard.advice.model import ContributionLine, Trade
from guard.ingest.model import Holding

ZERO = Decimal(0)
ONE = Decimal(1)


def dec(x: float) -> Decimal:
    """A float parameter as the Decimal it was written as."""
    return Decimal(str(x))


def whole(x: Decimal) -> Decimal:
    """Round to whole pounds, half to even."""
    return x.quantize(ONE, rounding=ROUND_HALF_EVEN)


def floor_pounds(x: Decimal) -> Decimal:
    return x.quantize(ONE, rounding=ROUND_DOWN)


def split(amount: Decimal, weights: list[tuple[str, Decimal]]) -> dict[str, Decimal]:
    """Split a whole-pound ``amount`` in proportion to ``weights``; the parts sum exactly."""
    total = sum((w for _, w in weights), ZERO)
    if amount <= 0 or total <= 0:
        return {}
    exact = [(s, amount * w / total) for s, w in weights]
    parts = {s: floor_pounds(x) for s, x in exact}
    left = int(amount - sum(parts.values(), ZERO))
    order = sorted(range(len(exact)), key=lambda i: (-(exact[i][1] - parts[exact[i][0]]), i))
    for i in order[:left]:
        parts[exact[i][0]] += ONE
    return {s: a for s, a in parts.items() if a > 0}


@dataclass(frozen=True)
class Book:
    positions: Mapping[tuple[str, str], Decimal]
    roles: Mapping[str, str]

    def by_symbol(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for (_, s), v in self.positions.items():
            out[s] = out.get(s, ZERO) + v
        return out

    def total(self) -> Decimal:
        return sum(self.positions.values(), ZERO)

    def role_total(self, role: str) -> Decimal:
        return sum((v for (_, s), v in self.positions.items() if self.roles.get(s) == role), ZERO)

    def growth(self) -> Decimal:
        return self.role_total("growth")

    def safe(self) -> Decimal:
        return self.role_total("safe")

    def held(self, account: str, symbol: str) -> Decimal:
        return self.positions.get((account, symbol), ZERO)

    def with_changes(self, changes: Iterable[tuple[str, str, Decimal]]) -> Book:
        pos = dict(self.positions)
        for account, symbol, delta in changes:
            pos[(account, symbol)] = pos.get((account, symbol), ZERO) + delta
        return Book(pos, self.roles)

    def with_trades(self, trades: Iterable[Trade]) -> Book:
        return self.with_changes(
            (t.account_id, t.symbol, t.amount if t.action == "BUY" else -t.amount) for t in trades
        )

    def with_lines(self, lines: Iterable[ContributionLine]) -> Book:
        return self.with_changes((c.account_id, c.symbol, c.amount) for c in lines)


def build_book(
    holdings: Iterable[Holding],
    funds: FundRegistry,
    safe_asset: str,
    extra_growth: Iterable[str] = (),
) -> Book:
    roles: dict[str, str] = {s: "growth" for s in extra_growth}
    roles[safe_asset] = "safe"
    pos: dict[tuple[str, str], Decimal] = {}
    for h in holdings:
        fund = funds.find(h.ticker)
        if fund is None or h.excluded:
            continue
        roles[fund.symbol] = fund.role
        key = (h.account_id, fund.symbol)
        pos[key] = pos.get(key, ZERO) + h.value_gbp
    return Book(pos, roles)
