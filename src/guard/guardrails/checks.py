"""Guardrails: pause switch, trade sanity, weekly cap and equity floor.

Every check is recorded in ``Advice.guardrails`` whether it passed or not. A failed sanity or
floor check blocks the advice: no trades, no contribution plan, and the reason says why.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from decimal import Decimal

from guard.advice.book import ZERO, Book, dec, floor_pounds, whole
from guard.advice.constraints import pct
from guard.advice.engine import (
    AdviceInputs,
    build_advice,
    isa_accounts,
    plan_contributions,
    portfolio_value,
    tactical_book,
)
from guard.advice.model import Advice, Flag, GuardrailCheck, Trade

FLOOR_TOLERANCE = Decimal("1e-9")


def advise(inputs: AdviceInputs) -> Advice:
    """Build the draft and run it through the guardrails."""
    return apply_guardrails(build_advice(inputs), inputs)


def check_trade_sanity(trades: tuple[Trade, ...], inputs: AdviceInputs) -> list[str]:
    """Reasons the trade list is unsafe; empty when it is fine."""
    problems: list[str] = []
    isa = set(isa_accounts(inputs.config))
    funds = inputs.funds
    held: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    excluded: set[str] = set()
    for h in inputs.holdings:
        symbol = funds.symbol_for(h.ticker)
        if h.excluded:
            excluded.add(symbol)
        else:
            held[(h.account_id, symbol)] += h.value_gbp
    sold: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    proceeds: dict[str, Decimal] = defaultdict(Decimal)
    bought: dict[str, Decimal] = defaultdict(Decimal)
    for t in trades:
        label = f"{t.action} {t.symbol} in {t.account_id}"
        if t.amount <= 0:
            problems.append(f"{label}: amount must be positive")
            continue
        if t.symbol in excluded:
            problems.append(f"{label}: excluded holding (fixed satellite) must not be traded")
        if t.action == "SELL":
            if t.account_id not in isa:
                problems.append(f"{label}: selling is only allowed in an ISA, never in a GIA")
            sold[(t.account_id, t.symbol)] += t.amount
            proceeds[t.account_id] += t.amount
        else:
            bought[t.account_id] += t.amount
            fund = funds.get(t.symbol)
            buyable = fund is not None and (
                fund.role == "growth" or t.symbol == inputs.config.portfolio.safe_asset
            )
            if not buyable:
                problems.append(
                    f"{label}: only growth funds in the fund reference and the safe asset "
                    "may be bought"
                )
    for (account, symbol), amount in sold.items():
        if amount > held[(account, symbol)]:
            problems.append(
                f"SELL {symbol} in {account}: more than the {held[(account, symbol)]} held"
            )
    for account, amount in bought.items():
        available = proceeds[account] + max(ZERO, inputs.cash.get(account, ZERO))
        if amount > available:
            problems.append(
                f"BUY in {account}: {amount} is more than sale proceeds plus cash ({available})"
            )
    return problems


def _scale(trades: tuple[Trade, ...], factor: Decimal, inputs: AdviceInputs) -> tuple[Trade, ...]:
    """Scale every trade down by ``factor``, rounding down so caps and cash still hold."""
    min_trade = Decimal(inputs.config.allocation.min_trade_gbp)
    out = [replace(t, amount=floor_pounds(t.amount * factor)) for t in trades]
    return tuple(t for t in out if t.amount > 0 and t.amount >= min_trade)


def _balance_accounts(trades: tuple[Trade, ...], inputs: AdviceInputs) -> tuple[Trade, ...]:
    """Trim buys that dropped trades left without funding."""
    room: dict[str, Decimal] = defaultdict(Decimal)
    for t in trades:
        if t.action == "SELL":
            room[t.account_id] += t.amount
    for account in {t.account_id for t in trades}:
        room[account] += floor_pounds(max(ZERO, inputs.cash.get(account, ZERO)))
    out: list[Trade] = []
    for t in sorted(trades, key=lambda t: t.action == "BUY"):
        if t.action == "BUY":
            amount = min(t.amount, room[t.account_id])
            room[t.account_id] -= amount
            if amount <= 0:
                continue
            t = replace(t, amount=amount)
        out.append(t)
    return tuple(out)


def _gap_flag(
    inputs: AdviceInputs, trades: tuple[Trade, ...], after: Book
) -> tuple[Flag | None, float]:
    """The rebalance gap left on the book once ``trades`` are made, and the growth share.

    The gap is the distance between the growth share and the advised equity weight, as a
    share of tactical value; it is flagged when it is worth at least a minimum trade.
    """
    total = after.total()
    if total <= 0:
        return None, 0.0
    share = after.growth() / total
    equity = dec(inputs.equity_after)
    remaining = abs(equity * total - after.growth())
    if remaining < max(Decimal(inputs.config.allocation.min_trade_gbp), Decimal(1)):
        return None, float(share)
    flag = Flag(
        "rebalance_gap",
        "warn",
        f"after the trades growth is {pct(share)} against the advised {pct(equity)}: "
        f"{pct(remaining / total)} of the tactical portfolio is still to move; "
        "it continues next week",
    )
    return flag, float(share)


def _blocked(
    draft: Advice, reasons: tuple[str, ...], checks: list[GuardrailCheck], flags: tuple[Flag, ...]
) -> Advice:
    return replace(
        draft,
        status="blocked",
        reasons=reasons,
        trades=(),
        contribution_plan=(),
        flags=flags,
        guardrails=tuple(checks),
    )


def apply_guardrails(draft: Advice, inputs: AdviceInputs) -> Advice:
    """Apply the guardrails in order: pause, trade sanity, weekly cap, equity floor."""
    cfg = inputs.config
    checks: list[GuardrailCheck] = []

    if inputs.paused:
        checks.append(GuardrailCheck("pause_switch", False, "advice is paused"))
        return replace(
            draft,
            status="paused",
            reasons=("advice is paused",),
            trades=(),
            contribution_plan=(),
            guardrails=tuple(checks),
        )
    checks.append(GuardrailCheck("pause_switch", True, "advice is not paused"))

    if draft.status == "blocked":
        checks.append(GuardrailCheck("book_complete", False, "; ".join(draft.reasons)))
        return replace(draft, guardrails=tuple(checks))

    flags = draft.flags
    trades = draft.trades
    problems = check_trade_sanity(trades, inputs)
    if problems:
        checks.append(GuardrailCheck("trade_sanity", False, "; ".join(problems)))
        return _blocked(draft, tuple(problems), checks, flags)
    checks.append(
        GuardrailCheck(
            "trade_sanity", True, "sells are ISA-only and within holdings; buys are funded"
        )
    )

    whole_value = portfolio_value(inputs)
    cap = dec(cfg.ladder.max_weekly_change) * whole_value
    sells = sum((t.amount for t in trades if t.action == "SELL"), ZERO)
    buys = sum((t.amount for t in trades if t.action == "BUY"), ZERO)
    biggest = max(sells, buys)
    plan = draft.contribution_plan
    if biggest > cap:
        factor = cap / biggest
        trades = _balance_accounts(_scale(trades, factor, inputs), inputs)
        label = f"capped at {pct(cfg.ladder.max_weekly_change, 0)} this week; continues next week"
        kept = tuple(f for f in flags if f.kind not in ("contribution", "rebalance_gap"))
        capped_book = tactical_book(inputs).with_trades(trades)
        plan, plan_flags = plan_contributions(inputs, capped_book)
        gap, _ = _gap_flag(inputs, trades, capped_book)
        flags = (*kept, Flag("weekly_cap", "warn", label), *plan_flags, *((gap,) if gap else ()))
        checks.append(
            GuardrailCheck(
                "weekly_cap",
                True,
                f"trades of {biggest} exceeded the cap of {whole(cap)}; all trades scaled by "
                f"{factor:.4f}",
                adjusted=True,
            )
        )
        problems = check_trade_sanity(trades, inputs)
        if problems:
            checks.append(GuardrailCheck("trade_sanity_after_cap", False, "; ".join(problems)))
            return _blocked(draft, tuple(problems), checks, flags)
    else:
        checks.append(
            GuardrailCheck(
                "weekly_cap", True, f"trades of {biggest} are within the cap of {whole(cap)}"
            )
        )

    before = tactical_book(inputs)
    after = before.with_trades(trades)
    total = after.total()
    share = after.growth() / total if total > 0 else None
    share_before = before.growth() / before.total() if before.total() > 0 else None
    floor = dec(cfg.ladder.equity_floor)
    if share is not None and share < floor - FLOOR_TOLERANCE:
        if share_before is not None and share >= share_before - FLOOR_TOLERANCE:
            note = (
                f"growth is {pct(share)} after trades, already below the {pct(floor, 0)} floor "
                f"({pct(share_before)} before); the trades do not lower it"
            )
            checks.append(GuardrailCheck("equity_floor", True, note))
        else:
            reason = (
                f"equity floor: growth would be {pct(share)} of tactical value, below the "
                f"{pct(floor, 0)} floor"
            )
            checks.append(GuardrailCheck("equity_floor", False, reason))
            return _blocked(draft, (reason,), checks, flags)
    else:
        shown = "no tactical holdings" if share is None else f"growth is {pct(share)} after trades"
        checks.append(GuardrailCheck("equity_floor", True, f"{shown} (floor {pct(floor, 0)})"))

    sleeve = replace(draft.sleeve, growth_after=float(share) if share is not None else 0.0)
    return replace(
        draft,
        trades=trades,
        contribution_plan=plan,
        flags=flags,
        sleeve=sleeve,
        guardrails=tuple(checks),
    )
