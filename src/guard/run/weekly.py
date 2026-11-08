"""The weekly run: collect, check, compute and store the advice in one go.

The slot is the Monday of the local ISO week, so a week has one slot and one ladder move however
many days the run is tried on. Nothing is stored until everything has succeeded. A
data-integrity problem records a ``stopped`` run (no ladder move, no advice) and returns it so the
caller can alert; any other error records ``failed`` and is re-raised.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from zoneinfo import ZoneInfo

from guard.advice.funds import symbol_of
from guard.ingest.model import Holding, IngestIntegrityError, Snapshot
from guard.ingest.snapshot_job import collect_snapshots
from guard.ingest.t212 import INSTRUMENTS, T212Client
from guard.ladder import initial_state
from guard.market.base import DataIntegrityError
from guard.market.store import MarketStore
from guard.run.audit import canonical_json, inputs_hash, weekly_payload
from guard.run.core import WeeklyInputs, compute_weekly, week_number
from guard.run.deps import Deps
from guard.run.live_series import build_live_levels
from guard.run.redact import redact_amounts
from guard.run.results import RunResult
from guard.storage import run_store, writer_lock
from guard.storage.ingest_store import store_raw_history, write_snapshot

log = logging.getLogger("guard.run")

KIND = "weekly"
LOCK_WAIT_SECONDS = 30.0
NO_HISTORY = "not enough price history for the signals"
PAUSED = "paused by the owner"


class SlotError(ValueError):
    """The requested slot is not in the current ISO week."""


def slot_for(local_today: date, requested: date | None = None) -> date:
    """The weekly slot: the Monday of the ISO week of ``local_today``. A ``requested`` date is
    accepted only inside that week (and maps to the same Monday)."""
    monday = local_today - timedelta(days=local_today.weekday())
    sunday = monday + timedelta(days=6)
    if requested is not None and not monday <= requested <= sunday:
        raise SlotError(
            f"slot {requested} is not in the current week ({monday} to {sunday}); "
            "only the current week can be run"
        )
    return monday


@dataclass
class _InstrumentRecorder:
    """Wraps a Trading212 client and keeps the instruments list it fetched, so the advice can
    tell which funds are tradeable without a second (rate-limited) request."""

    inner: T212Client
    instruments: list[Any] = field(default_factory=list)

    def get_all(self, path: str) -> list[Any]:
        items = self.inner.get_all(path)
        if path == INSTRUMENTS:
            self.instruments.extend(items)
        return items

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def run_weekly(
    deps: Deps, now: datetime, *, isa_lump_sum: Decimal = Decimal(0), slot: date | None = None
) -> RunResult:
    """Run the weekly advice for the current ISO week. ``slot`` may name any day of that week;
    a day of another week raises ``SlotError``."""
    local_today = now.astimezone(ZoneInfo(deps.config.schedule.timezone)).date()
    slot_day = slot_for(local_today, slot)
    with writer_lock("run", wait_seconds=LOCK_WAIT_SECONDS) as acquired:
        if not acquired:
            return RunResult("locked", KIND, slot_day.isoformat(), reason="another run is active")
        return _locked(deps, now, local_today, slot_day, isa_lump_sum)


def _locked(
    deps: Deps, now: datetime, today: date, slot_day: date, isa_lump_sum: Decimal
) -> RunResult:
    slot = slot_day.isoformat()
    conn = deps.ops
    existing = run_store.get_run_for_slot(conn, KIND, slot)

    def record(
        status: run_store.RunStatus, reason: str | None, **extra: Any
    ) -> run_store.RunRecord:
        return run_store.record_run(
            conn,
            kind=KIND,
            slot=slot,
            status=status,
            stop_reason=reason,
            config_version=deps.config_version,
            code_version=deps.code_version,
            started_at=now,
            finished_at=deps.clock(),
            **extra,
        )

    if run_store.get_paused(conn):
        if existing is not None and existing.status == "ok":
            # The stored advice stays as it is, but a paused system does not show it.
            return RunResult("paused", KIND, slot, reason=PAUSED)
        return RunResult("paused", KIND, slot, run=record("paused", PAUSED), reason=PAUSED)
    if existing is not None and existing.status == "ok":
        return RunResult("ok", KIND, slot, run=existing, last_close_date=_as_of(existing))

    try:
        return _compute_and_store(deps, now, today, slot_day, isa_lump_sum, record)
    except run_store.PausedError:
        return RunResult("paused", KIND, slot, run=record("paused", PAUSED), reason=PAUSED)
    except (DataIntegrityError, IngestIntegrityError) as e:
        reason = redact_amounts(str(e))
        log.warning("weekly run %s stopped: %s", slot, reason)
        return RunResult("stopped", KIND, slot, run=record("stopped", reason), reason=reason)
    except Exception as e:
        log.error("weekly run %s failed: %s", slot, type(e).__name__)
        try:
            record("failed", type(e).__name__)
        except Exception:  # noqa: BLE001 - never hide the original error
            log.exception("could not record the failed run")
        raise


def _as_of(run: run_store.RunRecord) -> date | None:
    """The last close date a stored weekly run was computed from."""
    try:
        days = json.loads(run.payload or "{}")["inputs"]["closes"]["days"]
        return date.fromisoformat(days[-1])
    except KeyError, IndexError, ValueError:
        return None


def _collect(
    deps: Deps, now: datetime, today: date
) -> tuple[list[Snapshot], dict[str, dict[str, Any]], frozenset[str] | None]:
    recorders: list[_InstrumentRecorder] = []

    def client_for(account_id: str) -> T212Client:
        recorder = _InstrumentRecorder(deps.client_for(account_id))
        recorders.append(recorder)
        return cast(T212Client, recorder)

    snapshots, history = collect_snapshots(now, today, deps.config, client_for, deps.manual_path)
    tickers = {str(i["ticker"]) for r in recorders for i in r.instruments}
    available = frozenset(symbol_of(t) for t in tickers) if tickers else None
    return snapshots, cast(dict[str, dict[str, Any]], history), available


def _compute_and_store(
    deps: Deps,
    now: datetime,
    today: date,
    slot_day: date,
    isa_lump_sum: Decimal,
    record: Any,
) -> RunResult:
    cfg = deps.config
    slot = slot_day.isoformat()
    snapshots, history, available = _collect(deps, now, today)
    holdings: tuple[Holding, ...] = tuple(h for s in snapshots for h in s.holdings)
    cash = {
        s.account_id: s.cash if s.spendable_cash is None else s.spendable_cash for s in snapshots
    }

    live = build_live_levels(
        cfg, deps.price_providers, deps.macro_providers, MarketStore(deps.warehouse), today
    )

    previous = run_store.latest_ladder_state(deps.ops)
    state_before = previous[1] if previous else initial_state(cfg.ladder)
    week = week_number(slot_day)
    contributions = {
        a.id: Decimal(cfg.portfolio.gia_monthly_contribution) if a.kind == "gia" else isa_lump_sum
        for a in cfg.portfolio.accounts
    }
    inputs = WeeklyInputs(
        config=cfg,
        days=live.days,
        levels=live.levels,
        state_before=state_before,
        week=week,
        holdings=holdings,
        cash=cash,
        contributions=contributions,
        funds=deps.funds(),
        paused=False,
        available_symbols=available,
    )
    result = compute_weekly(inputs)
    if result.decision.kind == "data_gap":
        log.warning("weekly run %s stopped: %s", slot, NO_HISTORY)
        return RunResult(
            "stopped", KIND, slot, run=record("stopped", NO_HISTORY), reason=NO_HISTORY
        )
    payload = weekly_payload(
        inputs,
        result,
        slot=slot,
        config_version=deps.config_version,
        code=deps.code_version,
        sources=live.sources,
    )

    # Everything succeeded: now store. Snapshots and raw history are idempotent facts; the run
    # row, new ladder state and advice commit together. The pause switch is read again here and
    # once more inside that transaction, since the owner may have paused during a long run.
    if run_store.get_paused(deps.ops):
        raise run_store.PausedError(slot)
    for snap in snapshots:
        write_snapshot(deps.ops, snap)
    for account_id, kinds in history.items():
        for kind, pairs in kinds.items():
            store_raw_history(deps.ops, kind, account_id, pairs, now)
    stored = record(
        "ok",
        None,
        inputs_hash=inputs_hash(payload["inputs"]),
        payload=canonical_json(payload),
        advice=canonical_json(payload["advice"]),
        ladder=(week, result.state_after),
        refuse_if_paused=True,
    )
    return RunResult(
        "ok",
        KIND,
        slot,
        run=stored,
        advice=result.advice,
        decision=result.decision,
        last_close_date=live.last_close_date,
    )
