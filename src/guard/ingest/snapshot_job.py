"""The scheduled daily snapshot: one function the scheduler calls, testable on its own."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from guard.config import GuardConfig, load_config
from guard.config.models import Schedule
from guard.ingest.manual import load_manual_holdings, snapshots_from_manual
from guard.ingest.model import IngestIntegrityError, Snapshot
from guard.ingest.t212 import (
    LIVE_BASE_URL,
    RawItems,
    T212Client,
    client_for_account,
    fetch_history,
    fetch_snapshot,
)
from guard.paths import data_dir
from guard.storage import open_ops, writer_lock
from guard.storage.ingest_store import (
    record_snapshot_run,
    snapshot_run_done,
    store_raw_history,
    write_snapshot,
)

Outcome = Literal["ok", "skipped", "stopped", "locked"]


def run_snapshot(
    now: datetime,
    *,
    config: GuardConfig,
    client_for: Callable[[str], T212Client],
    manual_path: Path | None = None,
) -> Outcome:
    """Snapshot every account for the local day of ``now`` and store raw history.

    A day already done is skipped. A data-integrity stop writes nothing and is recorded; the next
    call for the same day tries again. Days with no snapshot show up as gaps, never backfilled.
    """
    local = now.astimezone(ZoneInfo(config.schedule.timezone))
    day = local.date()
    slot = day.isoformat()
    with writer_lock() as acquired:
        if not acquired:
            return "locked"
        conn = open_ops()
        try:
            if snapshot_run_done(conn, slot):
                return "skipped"
            try:
                snapshots, history = _collect(now, day, config, client_for, manual_path)
            except IngestIntegrityError as e:
                record_snapshot_run(conn, slot, "stopped", str(e), now, datetime.now(UTC))
                return "stopped"
            except Exception as e:
                record_snapshot_run(conn, slot, "failed", type(e).__name__, now, datetime.now(UTC))
                raise
            for snap in snapshots:
                write_snapshot(conn, snap)
            for account_id, kinds in history.items():
                for kind, pairs in kinds.items():
                    store_raw_history(conn, kind, account_id, pairs, now)
            record_snapshot_run(conn, slot, "ok", None, now, datetime.now(UTC))
            return "ok"
        finally:
            conn.close()


def _collect(
    now: datetime,
    day: date,
    config: GuardConfig,
    client_for: Callable[[str], T212Client],
    manual_path: Path | None,
) -> tuple[list[Snapshot], dict[str, dict[str, RawItems]]]:
    satellites = config.portfolio.fixed_satellites
    tolerance = Decimal(str(config.data.crosscheck_tolerance))
    snapshots: list[Snapshot] = []
    history: dict[str, dict[str, RawItems]] = {}
    for account in config.portfolio.accounts:
        client = client_for(account.id)
        try:
            snapshots.append(
                fetch_snapshot(
                    client,
                    account,
                    satellites=satellites,
                    tolerance=tolerance,
                    taken_at=now,
                    day=day,
                )
            )
            history[account.id] = fetch_history(client)
        finally:
            client.close()
    if manual_path is not None:
        manual = load_manual_holdings(manual_path, satellites=satellites)
        snapshots.extend(snapshots_from_manual(manual, now, day))
    return snapshots, history


def daily_trigger(schedule: Schedule) -> CronTrigger:
    hour, minute = schedule.daily_check_time.split(":")
    return CronTrigger(hour=int(hour), minute=int(minute), timezone=ZoneInfo(schedule.timezone))


def build_scheduler(job: Callable[[datetime], object], schedule: Schedule) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=ZoneInfo(schedule.timezone))
    scheduler.add_job(lambda: job(datetime.now(UTC)), daily_trigger(schedule), id="daily-snapshot")
    return scheduler


def main() -> None:
    """Run the snapshot worker. ``GUARD_T212_BASE_URL`` selects the host (default: live)."""
    config, _ = load_config()
    base_url = os.environ.get("GUARD_T212_BASE_URL", LIVE_BASE_URL)
    manual = data_dir() / "manual_holdings.yaml"

    def job(now: datetime) -> Outcome:
        return run_snapshot(
            now,
            config=config,
            client_for=lambda account_id: client_for_account(account_id, base_url),
            manual_path=manual if manual.exists() else None,
        )

    build_scheduler(job, config.schedule).start()


if __name__ == "__main__":
    main()
