"""Advice: from holdings and the ladder's equity level to trades, a contribution plan and flags.

Public API (all pure; no I/O, clock or network):

* ``build_advice(inputs: AdviceInputs) -> Advice`` builds the draft. Rebalancing acts only when
  the ladder moved, the growth share drifted past ``rebalance.drift_band``, or a fund drifted
  from its ``allocation.growth_targets``. Sells happen only in ISA accounts; the GIA changes only
  through the contribution plan. Fixed satellites (``Holding.excluded``) are never traded and are
  left out of the tactical totals. Amounts are whole pounds; trades under
  ``allocation.min_trade_gbp`` are dropped.
* ``guard.guardrails.advise(inputs) -> Advice`` is the entry point to use: it builds the draft
  and applies the guardrails (pause, trade sanity, weekly cap, equity floor). Advice that did
  not pass them has status ``paused`` or ``blocked``, no trades and no contribution plan, and
  ``reasons`` says why. ``guard.guardrails.apply_guardrails(draft, inputs)`` is the second step
  on its own.
* ``AdviceInputs`` carries holdings (``guard.ingest.model.Holding``), spendable ``cash`` and
  next ``contributions`` per account id, ``equity_before`` / ``equity_after`` from the ladder
  (fractions), the ``FundRegistry``, the ``GuardConfig``, ``paused`` and ``available_symbols``
  (symbols found in the Trading212 instruments list, or ``None``).
* ``Advice`` holds ``status``, ``reasons``, ``trades``, ``contribution_plan``, ``allocation``,
  ``sleeve``, ``flags``, ``guardrails``, ``portfolio_value`` (all holdings) and
  ``tactical_value`` (non-excluded known funds).
* ``load_registry()`` reads the fund reference: packaged ``funds.yaml`` merged per symbol with
  ``$GUARD_DATA_DIR/config/funds.yaml`` when present.
* ``check_us_only``, ``check_fees`` and ``check_unknown`` are the constraint checks on their
  own; ``plan_contributions`` re-plans contributions for a given book of positions.

Instruments missing from the fund reference are flagged, never traded, and left out of the
tactical totals.
"""

from guard.advice.book import Book
from guard.advice.constraints import check_fees, check_unknown, check_us_only
from guard.advice.engine import (
    AdviceInputs,
    build_advice,
    isa_accounts,
    plan_contributions,
    portfolio_value,
    tactical_book,
)
from guard.advice.fund_registry import FundFileError, load_registry, packaged_registry
from guard.advice.funds import Fund, FundRegistry, symbol_of
from guard.advice.model import (
    Advice,
    AllocationRow,
    ContributionLine,
    Flag,
    GuardrailCheck,
    Sleeve,
    Trade,
)

__all__ = [
    "Advice",
    "AdviceInputs",
    "AllocationRow",
    "Book",
    "ContributionLine",
    "Flag",
    "Fund",
    "FundFileError",
    "FundRegistry",
    "GuardrailCheck",
    "Sleeve",
    "Trade",
    "build_advice",
    "check_fees",
    "check_unknown",
    "check_us_only",
    "isa_accounts",
    "load_registry",
    "packaged_registry",
    "plan_contributions",
    "portfolio_value",
    "symbol_of",
    "tactical_book",
]
