"""The daily light check: tell the owner about a sharp fall, never trade, never touch the ladder.

It looks at the same live series as the weekly run, as of the evening close. It returns events for
the alert layer; they carry percentages only, no money amounts and no account names.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from guard.market.base import DataIntegrityError
from guard.market.calendar import is_trading_day
from guard.market.store import MarketStore
from guard.run.audit import canonical_json, inputs_hash
from guard.run.core import TRADING_DAYS_PER_WEEK, compute_signals
from guard.run.deps import Deps
from guard.run.live_series import build_live_levels
from guard.run.redact import redact_amounts
from guard.run.results import DailyEvent, DailyResult
from guard.storage import run_store, writer_lock

log = logging.getLogger("guard.run")

KIND = "daily"
LOCK_WAIT_SECONDS = 30.0
_EPS = 1e-12
_INPUT_ROWS = 30


def run_daily(deps: Deps, now: datetime) -> DailyResult:
    """Check the day that ends at ``now`` (the local evening). A closed-market day is skipped."""
    day = now.astimezone(ZoneInfo(deps.config.schedule.timezone)).date()
    slot = day.isoformat()
    if not is_trading_day(day):
        return DailyResult("skipped", slot, reason="markets closed")
    with writer_lock("run", wait_seconds=LOCK_WAIT_SECONDS) as acquired:
        if not acquired:
            return DailyResult("locked", slot, reason="another run is active")
        return _locked(deps, now, day)


UNCONFIRMED_TEXT = " This move is not confirmed by the second source."
PAUSED_TEXT = "No fast-path guidance: advice is paused."


def _events(
    deps: Deps,
    day_return: float,
    week_return: float,
    vol: Any,
    *,
    paused: bool = False,
    unconfirmed: bool = False,
) -> list[DailyEvent]:
    check = deps.config.daily_check
    fast_drop = deps.config.ladder.fast_path_weekly_drop
    fast = week_return <= -fast_drop + _EPS and not paused
    if paused:
        fast_text = PAUSED_TEXT
    elif fast:
        fast_text = f"The fast path applies: the week's fall is past {fast_drop:.0%}."
    else:
        fast_text = f"The fast path does not apply: the week's fall is not past {fast_drop:.0%}."
    if unconfirmed:
        fast_text += UNCONFIRMED_TEXT
    out: list[DailyEvent] = []
    if day_return <= -check.day_drop + _EPS:
        out.append(
            DailyEvent(
                "day_drop",
                f"The market fell {-day_return:.1%} in a day (alert level {check.day_drop:.0%}). "
                + fast_text,
                fast,
            )
        )
    if week_return <= -check.week_drop + _EPS:
        out.append(
            DailyEvent(
                "week_drop",
                f"The market fell {-week_return:.1%} over five trading days "
                f"(alert level {check.week_drop:.0%}). " + fast_text,
                fast,
            )
        )
    if vol.negative:
        out.append(
            DailyEvent(
                "volatility",
                f"Volatility is {vol.value:.0%}, above the "
                f"{deps.config.signals.volatility_threshold:.0%} limit. " + fast_text,
                fast,
            )
        )
    return out


def _locked(deps: Deps, now: datetime, day: date) -> DailyResult:
    slot = day.isoformat()
    conn = deps.ops
    existing = run_store.get_run_for_slot(conn, KIND, slot)
    paused = run_store.get_paused(conn)
    # A stored result carries the guidance of its time; while paused it is computed afresh.
    if existing is not None and existing.status == "ok" and not paused:
        return _from_stored(slot, existing)

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

    try:
        # By the evening the day's sessions are complete, so the series runs through today.
        live = build_live_levels(
            deps.config,
            deps.price_providers,
            deps.macro_providers,
            MarketStore(deps.warehouse),
            day + timedelta(days=1),
            returns_check="advisory",
            history="daily",
            required_close=day,
        )
        if len(live.levels) <= TRADING_DAYS_PER_WEEK or live.weekly_return is None:
            raise DataIntegrityError(
                "history", deps.config.live_series.primary, (), "too few closes for the daily check"
            )
        day_return = live.levels[-1] / live.levels[-2] - 1.0
        signals = compute_signals(deps.config.signals, live.days, live.levels)
        events = _events(
            deps,
            day_return,
            live.weekly_return,
            signals.volatility,
            paused=paused,
            unconfirmed=live.unconfirmed,
        )
        payload = {
            "kind": KIND,
            "slot": slot,
            "last_close_date": live.last_close_date,
            "day_return": day_return,
            "week_return": live.weekly_return,
            "volatility": signals.volatility,
            "events": events,
            "unconfirmed": live.unconfirmed,
            "unconfirmed_note": live.unconfirmed_note,
            "paused": paused,
            "inputs": {
                "days": live.days[-_INPUT_ROWS:],
                "levels": live.levels[-_INPUT_ROWS:],
                "sources": live.sources,
            },
        }
        stored = record(
            "ok",
            None,
            payload=canonical_json(payload),
            inputs_hash=inputs_hash(payload["inputs"]),
        )
        return DailyResult(
            "ok",
            slot,
            run=stored,
            events=tuple(events),
            day_return=day_return,
            week_return=live.weekly_return,
            last_close_date=live.last_close_date,
            unconfirmed=live.unconfirmed,
            paused=paused,
        )
    except DataIntegrityError as e:
        reason = redact_amounts(str(e))
        log.warning("daily check %s stopped: %s", slot, reason)
        return DailyResult("stopped", slot, run=record("stopped", reason), reason=reason)
    except Exception as e:
        log.error("daily check %s failed: %s", slot, type(e).__name__)
        try:
            record("failed", type(e).__name__)
        except Exception:  # noqa: BLE001 - never hide the original error
            log.exception("could not record the failed run")
        raise


def _from_stored(slot: str, run: run_store.RunRecord) -> DailyResult:
    import json

    data = json.loads(run.payload or "{}")
    events = tuple(DailyEvent(e["kind"], e["message"], e["fast_path"]) for e in data["events"])
    return DailyResult(
        "ok",
        slot,
        run=run,
        events=events,
        day_return=data["day_return"],
        week_return=data["week_return"],
        last_close_date=date.fromisoformat(data["last_close_date"]),
        unconfirmed=bool(data.get("unconfirmed", False)),
        paused=bool(data.get("paused", False)),
    )
