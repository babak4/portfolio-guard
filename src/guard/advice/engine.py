"""Build advice from holdings and the ladder's equity level. Pure: no I/O, no clock."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal

from guard.advice.book import ZERO, Book, build_book, dec, floor_pounds, split, whole
from guard.advice.constraints import check_fees, check_unknown, check_us_only, pct
from guard.advice.funds import FundRegistry, symbol_of
from guard.advice.model import (
    Advice,
    AllocationRow,
    ContributionLine,
    Flag,
    Sleeve,
    Trade,
)
from guard.config.models import GuardConfig
from guard.ingest.model import Holding

MOVE_EPSILON = 1e-9
TARGET_SUM_TOLERANCE = 1e-6
FUNDS_FILE = "$GUARD_DATA_DIR/config/funds.yaml"

NO_TARGETS = "no per-fund targets set"
NO_GROWTH_REBALANCE = "cannot rebalance: no growth fund held and no targets set"
NO_GROWTH_PLACE = "cannot place growth contribution: no growth fund held and no targets set"


@dataclass(frozen=True)
class AdviceInputs:
    """Everything the advice builder needs.

    ``cash`` must be SPENDABLE cash per account (Trading212 ``availableToTrade``), not the
    account's cash total: rebalance buys draw on it, so reserved or pending cash must not be in
    it. ``contributions`` is the next contribution per account: money that is not in the
    account yet and is never already counted in ``cash``. It is steered by the contribution
    plan and never spent by the rebalance buys. ``available_symbols`` are the symbols found in
    the Trading212 instruments list, or ``None`` when that list was not fetched.
    """

    holdings: tuple[Holding, ...]
    cash: Mapping[str, Decimal]
    equity_before: float
    equity_after: float
    contributions: Mapping[str, Decimal]
    funds: FundRegistry
    config: GuardConfig
    paused: bool = False
    available_symbols: frozenset[str] | None = None


def unknown_holdings(inputs: AdviceInputs) -> tuple[str, ...]:
    """Symbols of held, non-excluded positions that are missing from the fund registry."""
    out = {
        symbol_of(h.ticker)
        for h in inputs.holdings
        if not h.excluded and h.value_gbp > 0 and inputs.funds.find(h.ticker) is None
    }
    return tuple(sorted(out))


def book_problems(inputs: AdviceInputs, book: Book) -> tuple[str, ...]:
    """Reasons the book or the targets are too incomplete to size trades on; empty when fine."""
    problems: list[str] = []
    for symbol in unknown_holdings(inputs):
        problems.append(
            f"{symbol} is held but not in the fund reference, so the portfolio cannot be sized: "
            f"add {symbol} to the funds file: {FUNDS_FILE}"
        )
    targets = inputs.config.allocation.growth_targets
    if targets:
        for symbol in sorted(targets):
            fund = inputs.funds.get(symbol)
            if fund is None or fund.role != "growth":
                problems.append(
                    f"target {symbol} is not a growth fund in the fund reference: fix "
                    f"growth_targets or add it to the funds file: {FUNDS_FILE}"
                )
        held = book.by_symbol()
        for symbol in sorted(held):
            if book.roles.get(symbol) == "growth" and held[symbol] > 0 and symbol not in targets:
                problems.append(
                    f"{symbol} is held but has no target: add it to growth_targets, "
                    "with 0 to sell it"
                )
        total = sum(targets.values())
        if abs(total - 1.0) > TARGET_SUM_TOLERANCE:
            problems.append(f"growth_targets sum to {total:g}, not 1: fix them")
    return tuple(problems)


def tactical_book(inputs: AdviceInputs) -> Book:
    cfg = inputs.config
    return build_book(
        inputs.holdings,
        inputs.funds,
        cfg.portfolio.safe_asset,
        cfg.allocation.growth_targets,
    )


def portfolio_value(inputs: AdviceInputs) -> Decimal:
    return sum((h.value_gbp for h in inputs.holdings), ZERO)


def isa_accounts(config: GuardConfig) -> tuple[str, ...]:
    return tuple(a.id for a in config.portfolio.accounts if a.kind == "isa")


def _growth_shares(cfg: GuardConfig, book: Book) -> dict[str, Decimal]:
    """Each growth fund's share of the growth sleeve: configured targets, else the current mix."""
    targets = cfg.allocation.growth_targets
    if targets:
        total = sum((dec(v) for v in targets.values()), ZERO)
        return {s: dec(v) / total for s, v in targets.items()} if total > 0 else {}
    held = {s: v for s, v in book.by_symbol().items() if book.roles.get(s) == "growth" and v > 0}
    total = sum(held.values(), ZERO)
    return {s: v / total for s, v in held.items()} if total > 0 else {}


def _target_values(cfg: GuardConfig, book: Book, equity: Decimal) -> dict[str, Decimal] | None:
    """Target value per fund, or ``None`` when no growth fund can be targeted."""
    shares = _growth_shares(cfg, book)
    if not shares:
        return None
    total = book.total()
    current = book.by_symbol()
    out = {s: equity * total * share for s, share in shares.items()}
    for s in current:
        if book.roles.get(s) == "growth":
            out.setdefault(s, ZERO)
    safe_asset = cfg.portfolio.safe_asset
    others = sum(
        (v for s, v in current.items() if book.roles.get(s) == "safe" and s != safe_asset), ZERO
    )
    for s, v in current.items():
        if book.roles.get(s) == "safe" and s != safe_asset:
            out[s] = v
    out[safe_asset] = max(ZERO, (1 - equity) * total - others)
    return out


def _needs_action(inputs: AdviceInputs, book: Book) -> bool:
    cfg = inputs.config
    if abs(inputs.equity_after - inputs.equity_before) > MOVE_EPSILON:
        return True
    total = book.total()
    if total <= 0:
        return False
    band = dec(cfg.rebalance.drift_band)
    equity = dec(inputs.equity_after)
    if abs(book.growth() / total - equity) > band:
        return True
    if cfg.allocation.growth_targets:
        shares = _growth_shares(cfg, book)
        current = book.by_symbol()
        for s in set(shares) | {s for s in current if book.roles.get(s) == "growth"}:
            weight = current.get(s, ZERO) / total
            if abs(weight - equity * shares.get(s, ZERO)) > band:
                return True
    return False


def _rebalance(
    inputs: AdviceInputs, book: Book, targets: dict[str, Decimal]
) -> tuple[tuple[Trade, ...], tuple[Flag, ...]]:
    cfg = inputs.config
    min_trade = Decimal(cfg.allocation.min_trade_gbp)
    isa = isa_accounts(cfg)
    current = book.by_symbol()
    delta = {s: targets.get(s, ZERO) - current.get(s, ZERO) for s in set(targets) | set(current)}

    trades: list[Trade] = []
    proceeds = dict.fromkeys(isa, ZERO)
    for s in sorted(delta):
        if delta[s] >= 0:
            continue
        want = whole(-delta[s])
        for account in isa:
            take = min(want, floor_pounds(book.held(account, s)))
            if take <= 0 or take < min_trade:
                continue
            trades.append(Trade(account, s, "SELL", take))
            proceeds[account] += take
            want -= take

    deficits = {s: d for s, d in delta.items() if d > 0}
    total_deficit = sum(deficits.values(), ZERO)
    cash = {a: floor_pounds(max(ZERO, inputs.cash.get(a, ZERO))) for a in isa}
    budget = sum(proceeds.values(), ZERO) + sum(cash.values(), ZERO)
    spend = min(budget, floor_pounds(total_deficit))

    placed = ZERO
    if spend > 0:
        needs = split(spend, sorted(deficits.items()))
        left = spend
        spend_by_account: dict[str, Decimal] = {}
        for account in isa:  # sale proceeds first, then cash
            amt = min(left, proceeds[account])
            spend_by_account[account] = amt
            left -= amt
        for account in isa:
            amt = min(left, cash[account])
            spend_by_account[account] += amt
            left -= amt
        order = sorted(needs, key=lambda s: (-needs[s], s))
        for account in isa:
            avail = spend_by_account[account]
            for s in order:
                amt = min(avail, needs[s])
                if amt <= 0:
                    continue
                needs[s] -= amt
                avail -= amt
                if amt >= min_trade:
                    trades.append(Trade(account, s, "BUY", amt))
                    placed += amt

    flags: list[Flag] = []
    remaining = total_deficit - placed
    total = book.total()
    if total > 0 and remaining >= max(min_trade, Decimal(1)):
        flags.append(
            Flag(
                "rebalance_gap",
                "warn",
                f"the ISA alone cannot reach the target mix: {pct(remaining / total)} of the "
                "tactical portfolio is still to move; new money will close it",
            )
        )
    return tuple(trades), tuple(flags)


def plan_contributions(
    inputs: AdviceInputs, book_after_trades: Book
) -> tuple[tuple[ContributionLine, ...], tuple[Flag, ...]]:
    """Steer each account's next contribution toward the safe or growth side.

    ``book_after_trades`` is the tactical book once the advised trades are made. Accounts are
    handled in configured order, each seeing the contributions placed before it. Lines are whole
    pounds and sum exactly to the contribution (except an unplaceable growth part, which is
    flagged).
    """
    cfg = inputs.config
    equity = dec(inputs.equity_after)
    safe_asset = cfg.portfolio.safe_asset
    configured = [a.id for a in cfg.portfolio.accounts]
    order = configured + sorted(a for a in inputs.contributions if a not in configured)
    book = book_after_trades
    lines: list[ContributionLine] = []
    flags: list[Flag] = []
    for account in order:
        amount = whole(inputs.contributions.get(account, ZERO))
        if amount <= 0:
            continue
        total, growth = book.total(), book.growth()
        gap = equity * (total + amount) - growth
        equity_part = min(max(whole(gap), ZERO), amount)
        safe_part = amount - equity_part
        placed: dict[str, Decimal] = {}
        if equity_part > 0:
            shares = _growth_shares(cfg, book)
            current = book.by_symbol()
            if not shares:
                flags.append(Flag("contribution", "warn", NO_GROWTH_PLACE))
            else:
                left = equity_part
                if cfg.allocation.growth_targets:
                    goal = equity * (total + amount)
                    deficits = {
                        s: goal * share - current.get(s, ZERO) for s, share in shares.items()
                    }
                    for s in sorted(
                        (s for s in deficits if deficits[s] > 0), key=lambda s: (-deficits[s], s)
                    ):
                        take = min(left, whole(deficits[s]))
                        if take > 0:
                            placed[s] = placed.get(s, ZERO) + take
                            left -= take
                for s, part in split(left, sorted(shares.items())).items():
                    placed[s] = placed.get(s, ZERO) + part
        if safe_part > 0:
            placed[safe_asset] = placed.get(safe_asset, ZERO) + safe_part
        new = [ContributionLine(account, s, a) for s, a in placed.items() if a > 0]
        new.sort(key=lambda c: (c.symbol == safe_asset, -c.amount, c.symbol))
        lines.extend(new)
        book = book.with_lines(new)
    return tuple(lines), tuple(flags)


def _allocation(
    inputs: AdviceInputs, book: Book, targets: dict[str, Decimal] | None, whole_value: Decimal
) -> tuple[AllocationRow, ...]:
    values: dict[str, Decimal] = {}
    excluded: set[str] = set()
    for h in inputs.holdings:
        s = inputs.funds.symbol_for(h.ticker)
        values[s] = values.get(s, ZERO) + h.value_gbp
        if h.excluded:
            excluded.add(s)
    for s in targets or {}:
        values.setdefault(s, ZERO)
    rows: list[AllocationRow] = []
    for s in sorted(values):
        fund = inputs.funds.get(s)
        role = fund.role if fund is not None else book.roles.get(s, "unknown")
        weight = values[s] / whole_value if whole_value > 0 else ZERO
        target: Decimal | None = None
        if targets is not None and s not in excluded and s in targets and whole_value > 0:
            target = targets[s] / whole_value
        rows.append(
            AllocationRow(
                s,
                role,
                values[s],
                float(weight),
                None if target is None else float(target),
                None if target is None else float(weight - target),
            )
        )
    return tuple(rows)


def _dedupe(flags: list[Flag]) -> tuple[Flag, ...]:
    seen: set[tuple[str, str]] = set()
    out: list[Flag] = []
    for f in flags:
        if (f.kind, f.message) not in seen:
            seen.add((f.kind, f.message))
            out.append(f)
    return tuple(out)


def build_advice(inputs: AdviceInputs) -> Advice:
    """The draft advice: trades, contribution plan, allocation and flags.

    The draft is not safe to show on its own; run it through
    ``guard.guardrails.apply_guardrails`` (or use ``guard.guardrails.advise``). When ``paused``
    is set the draft already carries no trades.
    """
    cfg = inputs.config
    book = tactical_book(inputs)
    total = book.total()
    whole_value = portfolio_value(inputs)
    equity = dec(inputs.equity_after)

    flags: list[Flag] = []
    us = check_us_only(inputs.holdings, inputs.funds, cfg.constraints.us_exposure_cap)
    if us is not None:
        flags.append(us)
    flags.extend(
        check_fees(
            inputs.holdings, inputs.funds, cfg.constraints.fee_ceiling, inputs.available_symbols
        )
    )
    flags.extend(check_unknown(inputs.holdings, inputs.funds))

    problems = book_problems(inputs, book)
    targets = _target_values(cfg, book, equity) if total > 0 else None
    trades: tuple[Trade, ...] = ()
    plan: tuple[ContributionLine, ...] = ()
    if not problems:
        if targets is not None and _needs_action(inputs, book):
            if not cfg.allocation.growth_targets:
                flags.append(Flag("targets", "info", NO_TARGETS))
            trades, gap_flags = _rebalance(inputs, book, targets)
            flags.extend(gap_flags)
        elif total > 0 and targets is None and _needs_action(inputs, book):
            flags.append(Flag("targets", "warn", NO_GROWTH_REBALANCE))
        plan, plan_flags = plan_contributions(inputs, book.with_trades(trades))
        flags.extend(plan_flags)
        if plan and not cfg.allocation.growth_targets:
            flags.append(Flag("targets", "info", NO_TARGETS))

    growth_actual = float(book.growth() / total) if total > 0 else 0.0
    safe_actual = float(book.safe() / total) if total > 0 else 0.0
    after = book.with_trades(trades)
    growth_after = float(after.growth() / after.total()) if after.total() > 0 else 0.0
    sleeve = Sleeve(growth_actual, safe_actual, float(equity), float(1 - equity), growth_after)
    advice = Advice(
        status="blocked" if problems else "advice",
        reasons=problems,
        trades=trades,
        contribution_plan=plan,
        allocation=_allocation(inputs, book, targets, whole_value),
        sleeve=sleeve,
        flags=_dedupe(flags),
        guardrails=(),
        portfolio_value=whole_value,
        tactical_value=total,
    )
    if inputs.paused:
        return replace(
            advice, status="paused", reasons=("advice is paused",), trades=(), contribution_plan=()
        )
    return advice
