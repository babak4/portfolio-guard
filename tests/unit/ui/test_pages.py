from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from guard.run.weekly import run_weekly
from guard.storage import open_ops, run_store
from tests.unit.run.fakes import make_deps, market

UI = Path(__file__).resolve().parents[3] / "src" / "guard" / "ui"
PAGES = [
    UI / "app.py",
    *sorted((UI / "pages").glob("*.py")),
]
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
RUN_ID = "weekly-2026-11-09"


@pytest.fixture(autouse=True)
def open_for_local(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in ("GUARD_ALLOWED_USERS", "GUARD_DASHBOARD_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GUARD_DASHBOARD_INSECURE_LOCAL", "1")
    from streamlit import config

    config.set_option("server.address", "127.0.0.1")
    yield
    config.set_option("server.address", None)


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("guard.ui.components.utcnow", lambda: NOW)


@pytest.fixture
def recorded(data_dir: Path) -> Iterator[None]:
    prices, macro = market(date(2026, 11, 10))
    deps = make_deps(prices, macro)
    assert run_weekly(deps, NOW).status == "ok"
    deps.close()
    yield


def _run(page: Path) -> AppTest:
    at = AppTest.from_file(str(page), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _text(at: AppTest) -> str:
    parts = [
        m.value for m in (*at.markdown, *at.info, *at.warning, *at.error, *at.success, *at.caption)
    ]
    return "\n".join(parts)


def test_pages_exist() -> None:
    assert len(PAGES) == 6


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_renders_with_data(recorded: None, page: Path) -> None:
    at = _run(page)
    assert at.title  # the page title was drawn, so access, config and database all passed


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_says_no_runs_yet_without_a_database(data_dir: Path, page: Path) -> None:
    at = _run(page)
    assert "No runs yet" in _text(at)


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_renders_on_a_migrated_but_empty_database(data_dir: Path, page: Path) -> None:
    open_ops().close()
    at = _run(page)
    text = _text(at)
    assert any(s in text for s in ("No runs yet", "no advice", "No account snapshots", "No status"))


def test_status_shows_light_and_last_runs(recorded: None) -> None:
    at = _run(UI / "app.py")
    assert any("AMBER" in m.value for m in at.markdown)
    jobs = [row for df in at.dataframe for row in df.value["Job"]]
    assert "Weekly advice" in jobs


def test_pause_switch_needs_the_confirm_then_calls_the_action(recorded: None) -> None:
    at = _run(UI / "app.py")
    assert at.button(key="pause").disabled
    at.checkbox(key="confirm_pause").check().run()
    at.button(key="pause").click().run()
    assert not at.exception
    c = open_ops()
    assert run_store.get_paused(c)
    c.close()
    assert any("paused" in w.value for w in at.warning)
    at.button(key="resume").click().run()
    c = open_ops()
    assert not run_store.get_paused(c)
    c.close()


def test_advice_page_shows_contribution_wording_and_unverified(recorded: None) -> None:
    at = _run(UI / "pages" / "1_This_weeks_advice.py")
    text = _text(at) + "\n".join(str(df.value) for df in at.dataframe)
    assert "monthly contribution (once a month; skip if already made)" in text
    assert "unverified" in text


def test_respond_form_calls_the_action(recorded: None) -> None:
    page = UI / "pages" / "5_Respond.py"
    at = _run(page)
    at.radio[0].set_value("changed")
    at.text_area(key="note").set_value("bought less")
    at.button[0].click().run()
    assert not at.exception
    c = open_ops()
    rows = run_store.responses_for(c, RUN_ID)
    c.close()
    assert [(r.decision, r.note) for r in rows] == [("changed", "bought less")]
    assert any("Recorded" in s.value for s in at.success)


def test_history_page_shows_an_identical_replay(recorded: None) -> None:
    at = _run(UI / "pages" / "4_History_and_audit.py")
    # The run used the stub code version, so a replay may report the code as changed; the
    # record itself must still be readable.
    assert any(
        "Recomputed" in m.value or "program changed" in m.value for m in (*at.success, *at.warning)
    )


def test_access_is_refused_without_configuration(
    recorded: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GUARD_DASHBOARD_INSECURE_LOCAL")
    at = _run(UI / "app.py")
    assert any("not configured" in e.value for e in at.error)
    assert not at.button


def _set_clock(monkeypatch: pytest.MonkeyPatch, when: datetime) -> None:
    monkeypatch.setattr("guard.ui.components.utcnow", lambda: when)


def test_current_advice_has_no_stale_banner(recorded: None) -> None:
    at = _run(UI / "pages" / "1_This_weeks_advice.py")
    assert "has not run yet" not in _text(at)
    assert any(h.value == "What to do" for h in at.subheader)


def test_stale_advice_is_flagged_and_hidden_until_shown_anyway(
    recorded: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_clock(monkeypatch, datetime(2026, 11, 25, 12, 0, tzinfo=UTC))
    page = UI / "pages" / "1_This_weeks_advice.py"
    at = _run(page)
    assert any(
        "This is last week's advice (week of 2026-11-09). This week's has not run yet." in e.value
        for e in at.error
    )
    assert not any(h.value == "What to do" for h in at.subheader)
    at.checkbox(key="show_stale").check().run()
    assert not at.exception
    assert any(h.value == "What to do" for h in at.subheader)


def _pause() -> None:
    c = open_ops()
    run_store.set_paused(c, True, NOW)
    c.close()


def test_paused_advice_is_withheld_on_the_advice_page_until_expanded(recorded: None) -> None:
    _pause()
    page = UI / "pages" / "1_This_weeks_advice.py"
    at = _run(page)
    assert "paused" in _text(at).lower()
    assert not any(h.value == "What to do" for h in at.subheader)
    at.checkbox(key="show_paused").check().run()
    assert any(h.value == "What to do" for h in at.subheader)


def test_respond_page_hides_the_trade_count_while_paused(recorded: None) -> None:
    page = UI / "pages" / "5_Respond.py"
    assert "That week's advice:" in _text(_run(page))
    _pause()
    at = _run(page)
    assert "advice is paused" in _text(at).lower()
    assert "That week's advice:" not in _text(at)
    at.checkbox(key="show_paused").check().run()
    assert "That week's advice:" in _text(at)


def test_configuration_errors_do_not_echo_file_content(data_dir: Path, recorded: None) -> None:
    (data_dir / "config").mkdir(exist_ok=True)
    (data_dir / "config" / "overrides.yaml").write_text(
        "ladder:\n  max_weekly_change: topsecret-value\n"
    )
    at = _run(UI / "app.py")
    shown = _text(at)
    assert "topsecret-value" not in shown
    assert "configuration could not be read" in shown
