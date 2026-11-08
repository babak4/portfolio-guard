from __future__ import annotations

import io
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from guard.run.deps import Deps
from guard.storage import run_store
from guard.worker.main import main
from tests.unit.run.fakes import make_deps, market

NOW = datetime(2026, 11, 11, 12, 0, 5, tzinfo=UTC)


class Cli:
    def __init__(self, last_day: date = date(2026, 11, 10), now: datetime = NOW) -> None:
        self.prices, self.macro = market(last_day)
        self.now = now

    def deps(self) -> Deps:
        return make_deps(self.prices, self.macro, now=self.now)

    def __call__(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        code = main(list(argv), deps_factory=self.deps, out=out)
        return code, out.getvalue()


@pytest.fixture
def cli(data_dir: Path) -> Cli:
    return Cli()


def test_run_now_weekly_prints_advice_and_the_run_id(cli: Cli) -> None:
    code, text = cli("run-now", "weekly")
    assert code == 0
    assert "weekly 2026-11-09: ok" in text
    assert "contribute to VWRP in gia: GBP 1000" in text
    assert "run id: weekly-2026-11-09" in text
    assert "as of close: 2026-11-10" in text
    assert re.search(r"advice hash: [0-9a-f]{12}\n", text)
    assert "monthly contribution (once a month; skip if already made)" in text


def test_run_now_weekly_takes_a_slot_in_this_week_and_a_lump_sum(cli: Cli) -> None:
    code, text = cli("run-now", "weekly", "--slot", "2026-11-13", "--isa-lump-sum", "2000")
    assert code == 0 and "weekly 2026-11-09: ok" in text
    assert "in isa: GBP 2000" in text


@pytest.mark.parametrize("slot", ["2026-11-02", "2026-11-16", "2027-01-01"])
def test_a_slot_outside_the_current_week_exits_2_with_a_clear_message(cli: Cli, slot: str) -> None:
    code, text = cli("run-now", "weekly", "--slot", slot)
    assert code == 2 and "not in the current week" in text
    assert cli.deps().ops.execute("SELECT COUNT(*) FROM run").fetchone()[0] == 0


def test_a_stopped_run_exits_nonzero_and_says_why(data_dir: Path) -> None:
    cli = Cli(last_day=date(2026, 11, 3))
    code, text = cli("run-now", "weekly")
    assert code == 2 and "stopped" in text and "staleness" in text
    assert re.search(r"\d{5}", text) is None
    assert re.search(r"\d{5}", text) is None


def test_run_now_daily_on_a_trading_day(data_dir: Path) -> None:
    cli = Cli(now=datetime(2026, 11, 10, 22, 30, tzinfo=UTC))
    code, text = cli("run-now", "daily")
    assert code == 0 and "daily 2026-11-10: ok" in text and "ALERT" not in text
    assert "as of close: 2026-11-10" in text and "run id: daily-2026-11-10" in text


def test_pause_and_resume_flip_the_switch_and_status_shows_it(cli: Cli) -> None:
    assert cli("pause") == (0, "paused: yes\n")
    assert cli("status")[1].startswith("paused: yes")
    code, text = cli("run-now", "weekly")
    assert "weekly 2026-11-09: paused" in text and "advice" not in text
    assert cli("resume") == (0, "paused: no\n")
    assert cli("status")[1].startswith("paused: no")
    assert "weekly 2026-11-09: ok" in cli("run-now", "weekly")[1]


def test_status_shows_the_age_of_the_last_successful_runs(cli: Cli) -> None:
    assert "weekly: never succeeded" in cli("status")[1]
    cli("run-now", "weekly")
    cli.now = NOW + timedelta(days=2, hours=3)
    code, text = cli("status")
    assert code == 0
    assert "weekly: last ok run 2026-11-09 (2d 3h ago)" in text
    assert "daily: never succeeded" in text and "snapshot: never succeeded" in text


def test_respond_records_and_a_later_response_supersedes(cli: Cli) -> None:
    cli("run-now", "weekly")
    assert cli("respond", "weekly-2026-11-09", "followed", "--note", "all done")[0] == 0
    assert cli("respond", "weekly-2026-11-09", "changed")[0] == 0
    rows = run_store.responses_for(cli.deps().ops, "weekly-2026-11-09")
    assert [r.decision for r in rows] == ["followed", "changed"]
    assert rows[0].note == "all done" and rows[0].supersedes is None
    assert rows[1].supersedes == rows[0].id


def test_respond_to_an_unknown_run_fails_cleanly(cli: Cli) -> None:
    code, text = cli("respond", "weekly-nope", "ignored")
    assert code == 1 and "no such run" in text


def test_respond_rejects_an_unknown_decision(cli: Cli) -> None:
    with pytest.raises(SystemExit):
        cli("respond", "weekly-2026-11-09", "maybe")


def test_a_bad_lump_sum_is_refused(cli: Cli) -> None:
    code, text = cli("run-now", "weekly", "--isa-lump-sum", "lots")
    assert code == 1 and "not an amount" in text


def test_respond_shows_the_short_hash_of_the_advice_it_is_tied_to(cli: Cli) -> None:
    _, shown = cli("run-now", "weekly")
    short = re.search(r"advice hash: ([0-9a-f]{12})", shown)
    assert short
    code, text = cli("respond", "weekly-2026-11-09", "followed")
    assert code == 0 and short.group(1) in text


def test_respond_to_a_stopped_run_says_only_ok_weekly_runs_take_a_response(data_dir: Path) -> None:
    cli = Cli(last_day=date(2026, 11, 3))
    cli("run-now", "weekly")
    code, text = cli("respond", "weekly-2026-11-09", "followed")
    assert code == 1 and "only an ok weekly run" in text


def test_a_paused_system_does_not_show_stored_advice(cli: Cli) -> None:
    cli("run-now", "weekly")
    cli("pause")
    code, text = cli("run-now", "weekly")
    assert "paused" in text and "contribute" not in text and "advice hash" not in text


def test_a_daily_check_while_paused_says_advice_is_paused(data_dir: Path) -> None:
    cli = Cli(now=datetime(2026, 11, 10, 22, 30, tzinfo=UTC))
    cli("pause")
    code, text = cli("run-now", "daily")
    assert code == 0 and "advice is paused" in text


def test_status_check_is_healthy_before_any_run_and_after_this_weeks_run(cli: Cli) -> None:
    assert cli("status", "--check")[0] == 0  # a fresh install is not overdue
    assert cli("run-now", "weekly")[0] == 0
    assert cli("status", "--check")[0] == 0


def test_status_check_fails_when_this_weeks_advice_is_overdue(data_dir: Path) -> None:
    done = Cli(now=datetime(2026, 11, 11, 12, 0, 5, tzinfo=UTC))
    assert done("run-now", "weekly")[0] == 0
    later = Cli(now=datetime(2026, 11, 18, 20, 0, tzinfo=UTC))  # next Wednesday evening, no run
    code, text = later("status", "--check")
    assert code == 1
    assert "has not completed" in text


def test_the_cli_installs_the_log_filter_so_bot_tokens_never_reach_the_logs(
    cli: Cli, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    cli("status")
    with caplog.at_level(logging.WARNING):
        logging.getLogger("apprise").error(
            "send failed: https://api.telegram.org/bot123456:ABCdef_gh-IJK/sendMessage"
        )
    assert "ABCdef_gh-IJK" not in caplog.text
