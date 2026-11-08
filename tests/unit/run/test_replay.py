from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from guard.advice.model import Advice
from guard.config.store import _versions_dir
from guard.run import core
from guard.run.replay import ReplayError, replay
from guard.run.weekly import run_weekly
from guard.storage import run_store

from .fakes import make_deps, market

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
RUN_ID = "weekly-2026-11-09"


@pytest.fixture
def deps(data_dir: Path):  # type: ignore[no-untyped-def]
    prices, macro = market(date(2026, 11, 10))
    d = make_deps(prices, macro)
    assert run_weekly(d, NOW).status == "ok"
    yield d
    d.close()


def _tamper(conn, sql: str, params: tuple = ()) -> None:  # type: ignore[no-untyped-def]
    """Edit the audit tables as someone with direct file access would: the triggers that stop
    the application editing them are dropped first."""
    for name in ("advice_no_update", "run_ok_no_update"):
        conn.execute(f"DROP TRIGGER {name}")
    conn.execute(sql, params)


def test_stored_run_replays_identical(deps) -> None:  # type: ignore[no-untyped-def]
    result = replay(deps.ops, RUN_ID, current_code_version="test-code")
    assert result.status == "identical" and result.diff == {}


def test_a_tampered_stored_advice_is_different(deps) -> None:  # type: ignore[no-untyped-def]
    row = run_store.get_run(deps.ops, RUN_ID)
    payload = json.loads(row.payload or "")  # type: ignore[union-attr]
    payload["advice"]["contribution_plan"][0]["amount"] = "5000"
    _tamper(
        deps.ops,
        "UPDATE run SET payload = ? WHERE run_id = ?",
        (json.dumps(payload), RUN_ID),
    )
    result = replay(deps.ops, RUN_ID, current_code_version="test-code")
    assert result.status == "different" and "advice" in result.diff


def test_a_tampered_advice_row_is_different(deps) -> None:  # type: ignore[no-untyped-def]
    _tamper(deps.ops, "UPDATE advice SET payload = '{}' WHERE run_id = ?", (RUN_ID,))
    assert replay(deps.ops, RUN_ID, current_code_version="test-code").status == "different"


def test_tampered_inputs_are_caught_by_the_hash(deps) -> None:  # type: ignore[no-untyped-def]
    row = run_store.get_run(deps.ops, RUN_ID)
    payload = json.loads(row.payload or "")  # type: ignore[union-attr]
    payload["inputs"]["cash"]["isa"] = "1"
    _tamper(
        deps.ops,
        "UPDATE run SET payload = ? WHERE run_id = ?",
        (json.dumps(payload), RUN_ID),
    )
    result = replay(deps.ops, RUN_ID, current_code_version="test-code")
    assert result.status == "different" and "inputs_hash" in result.diff


def test_changed_code_that_changes_the_output_is_code_changed(  # type: ignore[no-untyped-def]
    deps, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    real = core.advise

    def other(inputs):  # type: ignore[no-untyped-def]
        out: Advice = real(inputs)
        return replace(out, reasons=("new wording",))

    with monkeypatch.context() as patched:
        patched.setattr(core, "advise", other)
        assert replay(deps.ops, RUN_ID, current_code_version="new-code").status == "code_changed"
    # the same output under a new code version is still identical
    assert replay(deps.ops, RUN_ID, current_code_version="new-code").status == "identical"


def test_an_archived_config_that_no_longer_matches_its_version_hash_is_different(  # type: ignore[no-untyped-def]
    deps,
) -> None:
    row = run_store.get_run(deps.ops, RUN_ID)
    version = json.loads(row.payload or "")["config_version"]  # type: ignore[union-attr]
    archive = _versions_dir() / f"{version}.json"
    data = json.loads(archive.read_text("utf-8"))
    data["portfolio"]["gia_monthly_contribution"] = 1
    archive.write_text(json.dumps(data), "utf-8")
    result = replay(deps.ops, RUN_ID, current_code_version="test-code")
    assert result.status == "different"
    assert "config_version" in result.diff
    assert "does not match" in (result.message or "")


def test_stored_objects_that_can_no_longer_be_rebuilt_are_code_changed_not_a_crash(  # type: ignore[no-untyped-def]
    deps,
) -> None:
    row = run_store.get_run(deps.ops, RUN_ID)
    payload = json.loads(row.payload or "")  # type: ignore[union-attr]
    payload["inputs"]["funds"][0]["field_removed_from_fund"] = "x"
    _tamper(
        deps.ops,
        "UPDATE run SET payload = ? WHERE run_id = ?",
        (json.dumps(payload), RUN_ID),
    )
    result = replay(deps.ops, RUN_ID, current_code_version="test-code")
    assert result.status == "code_changed"
    assert "field_removed_from_fund" in (result.message or "")


def test_runs_without_advice_cannot_be_replayed(deps) -> None:  # type: ignore[no-untyped-def]
    run_store.set_paused(deps.ops, True, NOW)
    from guard.run.weekly import run_weekly as again

    again(deps, datetime(2026, 11, 18, 12, tzinfo=UTC))
    with pytest.raises(ReplayError):
        replay(deps.ops, "weekly-2026-11-16")
    with pytest.raises(ReplayError):
        replay(deps.ops, "weekly-nope")
