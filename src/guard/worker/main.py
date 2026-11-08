"""Command line for the worker: run the scheduler, run a job now, pause, resume, status, respond.

Output goes to the owner's own terminal, so advice is shown with amounts. Logs never carry
secrets.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TextIO

from guard.advice import Advice
from guard.notify.deliver import (
    NOT_CONFIGURED,
    AppriseSender,
    Sender,
    channels_from_secrets,
    deliver,
)
from guard.notify.render import render_test
from guard.run.daily import run_daily
from guard.run.deps import Deps, default_deps
from guard.run.redact import redact_amounts
from guard.run.results import DailyResult, RunResult
from guard.run.weekly import SlotError, run_weekly
from guard.storage import run_store, writer_lock
from guard.storage.alert_store import AlertStore
from guard.worker.schedule import build_scheduler
from guard.worker.status import build_status, format_status

LOCK_WAIT_SECONDS = 30.0
EXIT_OK, EXIT_FAILED, EXIT_STOPPED, EXIT_LOCKED = 0, 1, 2, 3
SHORT_HASH = 12
CONTRIBUTION_LABEL = "monthly contribution (once a month; skip if already made)"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m guard.worker", description=__doc__)
    sub = parser.add_subparsers(dest="command")
    now = sub.add_parser("run-now", help="run a job now instead of waiting for its schedule")
    now.add_argument("job", choices=["weekly", "daily"])
    now.add_argument(
        "--slot",
        type=date.fromisoformat,
        help="weekly only: a date in the current week (the slot is that week's Monday)",
    )
    now.add_argument("--isa-lump-sum", default="0", help="weekly only: ISA money to place, in GBP")
    sub.add_parser("pause", help="stop all advice until resumed")
    sub.add_parser("resume", help="allow advice again")
    sub.add_parser("status", help="pause switch and the last successful run of each job")
    sub.add_parser("test-alert", help="send a test message through the real alert channels")
    respond = sub.add_parser("respond", help="record what you did with a piece of advice")
    respond.add_argument("run_id")
    respond.add_argument("decision", choices=list(run_store.DECISIONS))
    respond.add_argument("--note", default=None)
    return parser


def format_advice(advice: Advice) -> str:
    lines = [f"advice: {advice.status}"]
    lines += [f"  reason: {r}" for r in advice.reasons]
    for t in advice.trades:
        lines.append(f"  {t.action} {t.symbol} in {t.account_id}: GBP {t.amount}")
    for c in advice.contribution_plan:
        lines.append(
            f"  {CONTRIBUTION_LABEL}: contribute to {c.symbol} in {c.account_id}: GBP {c.amount}"
        )
    lines += [f"  {f.severity}: {f.message}" for f in advice.flags]
    return "\n".join(lines)


def _show_weekly(result: RunResult, conn: sqlite3.Connection, out: TextIO) -> int:
    print(f"weekly {result.slot}: {result.status}", file=out)
    if result.reason:
        print(f"  {redact_amounts(result.reason)}", file=out)
    shown = result.status == "ok" and result.run is not None
    if shown and result.last_close_date is not None:
        print(f"  as of close: {result.last_close_date}", file=out)
    if result.decision is not None:
        d = result.decision
        print(f"  ladder: {d.kind}, equity {d.equity_before:.0%} -> {d.equity_after:.0%}", file=out)
    if result.advice is not None:
        print(format_advice(result.advice), file=out)
    if shown and result.run is not None:
        print(f"  run id: {result.run.run_id}", file=out)
        ref = run_store.advice_ref(conn, result.run.run_id)
        if ref is not None:
            print(f"  advice hash: {ref[:SHORT_HASH]}", file=out)
    return {"stopped": EXIT_STOPPED, "locked": EXIT_LOCKED}.get(result.status, EXIT_OK)


def _show_daily(result: DailyResult, out: TextIO) -> int:
    print(f"daily {result.slot}: {result.status}", file=out)
    if result.reason:
        print(f"  {redact_amounts(result.reason)}", file=out)
    if result.last_close_date is not None:
        print(f"  as of close: {result.last_close_date}", file=out)
    if result.day_return is not None and result.week_return is not None:
        print(f"  day {result.day_return:+.1%}, week {result.week_return:+.1%}", file=out)
    if result.unconfirmed:
        print("  not confirmed by the second source", file=out)
    if result.paused:
        print("  advice is paused", file=out)
    for event in result.events:
        print(f"  ALERT {event.kind}: {event.message}", file=out)
    if result.run is not None and result.status == "ok":
        print(f"  run id: {result.run.run_id}", file=out)
    return {"stopped": EXIT_STOPPED, "locked": EXIT_LOCKED}.get(result.status, EXIT_OK)


def _alert_status(conn: sqlite3.Connection) -> str:
    channels = channels_from_secrets()
    if channels:
        lines = [f"alerts: configured ({', '.join(c.name for c in channels)})"]
    else:
        lines = ["alerts: not configured (see the Alerts section of the README)"]
    last = {d.channel: d for d in AlertStore(conn).last_per_channel()}
    for channel in channels:
        d = last.get(channel.name)
        if d is None:
            lines.append(f"alert {channel.name}: never used")
        else:
            lines.append(
                f"alert {channel.name}: {'ok' if d.ok else 'failed'} {d.created_at} ({d.kind})"
            )
    return "\n".join(lines)


def _test_alert(deps: Deps, now: datetime, out: TextIO, sender: Sender | None) -> int:
    channels = channels_from_secrets()
    if not channels:
        AlertStore(deps.ops).record(
            kind="test", urgency="info", channel="none", ok=False, error=NOT_CONFIGURED, now=now
        )
        print("alerts are not configured; see the Alerts section of the README", file=out)
        return EXIT_FAILED
    result = deliver(render_test(), sender or AppriseSender(), channels, AlertStore(deps.ops), now)
    for attempt in result.attempts:
        print(f"{attempt.channel}: {'ok' if attempt.ok else 'failed'}", file=out)
    if result.delivered:
        print(f"test alert sent through {result.channel}", file=out)
        return EXIT_OK
    print("test alert failed on every channel; see the log (no secrets are shown)", file=out)
    return EXIT_FAILED


def main(
    argv: Sequence[str] | None = None,
    *,
    deps_factory: Callable[[], Deps] = default_deps,
    out: TextIO | None = None,
    sender: Sender | None = None,
) -> int:
    out = out or sys.stdout
    args = _parser().parse_args(argv)
    if args.command is None:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
        logging.getLogger("httpx").setLevel(logging.WARNING)
        build_scheduler(deps_factory).start()
        return EXIT_OK

    deps = deps_factory()
    try:
        now = deps.clock()
        if args.command == "run-now":
            if args.job == "weekly":
                try:
                    lump = Decimal(args.isa_lump_sum)
                except InvalidOperation:
                    print(f"not an amount: {args.isa_lump_sum}", file=out)
                    return EXIT_FAILED
                try:
                    weekly = run_weekly(deps, now, isa_lump_sum=lump, slot=args.slot)
                except SlotError as e:
                    print(str(e), file=out)
                    return EXIT_STOPPED
                return _show_weekly(weekly, deps.ops, out)
            return _show_daily(run_daily(deps, now), out)
        if args.command == "status":
            print(format_status(build_status(deps.ops, now)), file=out)
            print(_alert_status(deps.ops), file=out)
            return EXIT_OK
        if args.command == "test-alert":
            return _test_alert(deps, now, out, sender)
        with writer_lock("run", wait_seconds=LOCK_WAIT_SECONDS) as acquired:
            if not acquired:
                print("another run is active; try again shortly", file=out)
                return EXIT_LOCKED
            if args.command in ("pause", "resume"):
                run_store.set_paused(deps.ops, args.command == "pause", now)
                print("paused: yes" if args.command == "pause" else "paused: no", file=out)
                return EXIT_OK
            try:
                row = run_store.record_response(
                    deps.ops, args.run_id, args.decision, args.note, now
                )
            except run_store.UnknownRun:
                print(f"no such run: {args.run_id}", file=out)
                return EXIT_FAILED
            except run_store.NotRespondable as e:
                print(str(e), file=out)
                return EXIT_FAILED
            ref = run_store.advice_ref(deps.ops, args.run_id) or ""
            print(
                f"recorded {args.decision} for {args.run_id} "
                f"(entry {row}, advice hash {ref[:SHORT_HASH]})",
                file=out,
            )
            return EXIT_OK
    finally:
        deps.close()


if __name__ == "__main__":
    raise SystemExit(main())
