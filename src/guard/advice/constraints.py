"""Portfolio constraints as pure checks that return flags. They never change trades."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal

from guard.advice.funds import Fund, FundRegistry, symbol_of
from guard.advice.model import Flag
from guard.ingest.model import Holding

ZERO = Decimal(0)


def pct(fraction: float | Decimal, places: int = 1) -> str:
    return f"{Decimal(str(fraction)) * 100:.{places}f}%"


def _symbol(h: Holding, funds: FundRegistry) -> str:
    return funds.symbol_for(h.ticker)


def _values_by_symbol(holdings: Iterable[Holding], funds: FundRegistry) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for h in holdings:
        s = _symbol(h, funds)
        out[s] = out.get(s, ZERO) + h.value_gbp
    return out


def check_us_only(holdings: Sequence[Holding], funds: FundRegistry, cap: float) -> Flag | None:
    """Warn when funds flagged ``us_only`` exceed ``cap`` of the whole portfolio.

    The whole portfolio includes fixed satellites. US exposure inside global funds is not
    counted. Exactly at the cap is not a warning. Returns an info line otherwise, or ``None``
    for an empty portfolio.
    """
    values = _values_by_symbol(holdings, funds)
    total = sum(values.values(), ZERO)
    if total <= 0:
        return None
    us = {
        s: v for s, v in values.items() if (f := funds.get(s)) is not None and f.us_only and v > 0
    }
    share = sum(us.values(), ZERO) / total
    names = tuple(sorted(us, key=lambda s: (-us[s], s)))
    if share > Decimal(str(cap)):
        parts = ", ".join(f"{s} {pct(us[s] / total)}" for s in names)
        return Flag(
            "us_exposure",
            "warn",
            f"US-only funds are {pct(share)} of the whole portfolio, above the {pct(cap, 0)} "
            f"limit ({parts})",
            names,
        )
    return Flag(
        "us_exposure",
        "info",
        f"US-only funds are {pct(share)} of the whole portfolio (limit {pct(cap, 0)})",
        names,
    )


def _available(fund: Fund, available_symbols: frozenset[str] | None) -> bool:
    if available_symbols is not None:
        return fund.symbol in available_symbols
    return fund.available_on_t212 is True


def _figure(fund: Fund) -> str:
    """The fund's charge as text, marked when the figure has not been checked."""
    assert fund.ongoing_charge is not None
    mark = "" if fund.verified else " (unverified figure)"
    return f"{pct(fund.ongoing_charge, 2)}{mark}"


def _fmt_charge(fund: Fund) -> str:
    if fund.ongoing_charge is None:
        return f"{fund.symbol} (fee unknown)"
    return f"{fund.symbol} ({_figure(fund)})"


def check_fees(
    holdings: Sequence[Holding],
    funds: FundRegistry,
    ceiling: float,
    available_symbols: frozenset[str] | None,
) -> tuple[Flag, ...]:
    """Flag held funds charging more than ``ceiling``, with cheaper alternatives.

    Alternatives track the same index (same non-null ``index_group``), share the fund's role,
    have a known charge strictly lower than the held fund's and are available on Trading212
    (per ``available_symbols`` when given, otherwise the registry flag; unknown means not
    suggested). Those at or below the ceiling come first. A fund with an unknown charge gets an
    info line saying so and is never called cheap or expensive. Charges that are not verified
    say so. Fixed satellites and unknown funds are skipped. For a safe fund above the ceiling,
    the other money-market funds are listed as options, with a note that they may differ.
    """
    limit = Decimal(str(ceiling))
    flags: list[Flag] = []
    seen: set[str] = set()
    for h in holdings:
        fund = funds.find(h.ticker)
        if fund is None or h.excluded or fund.symbol in seen or h.value_gbp <= 0:
            continue
        seen.add(fund.symbol)
        if fund.ongoing_charge is None:
            flags.append(
                Flag(
                    "fee",
                    "info",
                    f"fee unknown for {fund.symbol}: check its factsheet",
                    (fund.symbol,),
                )
            )
            continue
        if Decimal(str(fund.ongoing_charge)) <= limit:
            continue
        head = f"{fund.symbol} charges {_figure(fund)}, above the {pct(ceiling, 2)} fee ceiling"
        if fund.role == "safe":
            options = sorted(
                (
                    f
                    for f in funds
                    if f.role == "safe"
                    and f.category == fund.category
                    and f.symbol != fund.symbol
                    and _available(f, available_symbols)
                ),
                key=lambda f: (f.ongoing_charge is None, f.ongoing_charge or 0.0, f.symbol),
            )
            tail = (
                "; money-market options on Trading212, your choice (they may differ in what they "
                "hold, so check before switching): " + ", ".join(_fmt_charge(f) for f in options)
                if options
                else "; no other money-market fund is confirmed available on Trading212"
            )
        else:
            cheaper = [f for f in funds.alternatives(fund) if _available(f, available_symbols)]
            cheaper.sort(
                key=lambda f: (
                    Decimal(str(f.ongoing_charge)) > limit,
                    f.ongoing_charge or 0.0,
                    f.symbol,
                )
            )
            options = cheaper
            tail = (
                "; cheaper on Trading212: " + ", ".join(_fmt_charge(f) for f in options)
                if options
                else "; no cheaper fund in the same category is confirmed available on Trading212"
            )
        flags.append(Flag("fee", "warn", head + tail, (fund.symbol, *(f.symbol for f in options))))
    return tuple(flags)


def check_unknown(holdings: Sequence[Holding], funds: FundRegistry) -> tuple[Flag, ...]:
    """Warn about held instruments missing from the fund reference."""
    flags: list[Flag] = []
    seen: set[str] = set()
    for h in holdings:
        if funds.find(h.ticker) is not None:
            continue
        s = symbol_of(h.ticker)
        if s in seen:
            continue
        seen.add(s)
        flags.append(
            Flag(
                "unknown_fund",
                "warn",
                f"unknown fund {s}: add it to the funds file; it is not covered by the US and "
                "fee checks",
                (s,),
            )
        )
    return tuple(flags)
