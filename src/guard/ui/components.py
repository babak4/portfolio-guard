"""Widgets and wording shared by every page."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import streamlit as st

from guard import readapi
from guard.config import GuardConfig, load_config
from guard.paths import DataDirError
from guard.readapi.models import AllocationLine, SignalView
from guard.readapi.status import expected_weekly_slot
from guard.report import Light
from guard.ui import auth

LIGHT_COLOURS = {"green": "#1a7f37", "amber": "#b7791f", "red": "#c62828"}
LIGHT_WORDS = {
    "green": "All clear. Nothing to do.",
    "amber": "Take a look at this week's advice.",
    "red": "Something needs your attention.",
}
JOB_NAMES = {
    "weekly": "Weekly advice",
    "daily": "Daily market check",
    "snapshot": "Daily account snapshot",
}
DECISION_WORDS = {
    "followed": "I followed the advice",
    "changed": "I did something different",
    "ignored": "I did nothing",
}
NO_RUNS = (
    "No runs yet. The first weekly run creates the history this dashboard shows "
    "(`make weekly` runs it now)."
)
FLASH_KEY = "guard_flash"
CONFIG_UNREADABLE = (
    "The configuration could not be read. Check config/overrides.yaml in the data directory "
    "on the host."
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def stale_week(cfg: GuardConfig, slot: str, now: datetime) -> date | None:
    """The expected weekly slot when ``slot`` is older than it (this week's run is missing)."""
    expected = expected_weekly_slot(cfg, now)
    try:
        return expected if date.fromisoformat(slot) < expected else None
    except ValueError:
        return None


@dataclass(frozen=True)
class Ctx:
    conn: Any  # a read-only database connection from guard.readapi
    cfg: GuardConfig


@contextmanager
def page(title: str, *, needs_database: bool = True) -> Iterator[Ctx]:
    """Common start of a page: title, access check, configuration, read-only connection.

    Stops the script with a friendly message when access is refused, the data directory is not
    set, or (with ``needs_database``) no run has created the database yet.
    """
    st.set_page_config(page_title=f"Portfolio Guard - {title}", layout="wide")
    auth.require_access()
    try:
        cfg = load_config()[0]
    except DataDirError:
        st.error("GUARD_DATA_DIR is not set, so there is no data to show.")
        st.stop()
    except ValueError:  # a configuration the owner can fix; the text may quote file content
        st.error(CONFIG_UNREADABLE)
        st.stop()
    st.title(title)
    flash = st.session_state.pop(FLASH_KEY, None)
    if flash:
        (st.success if flash[0] else st.error)(flash[1])
    with readapi.reader() as conn:
        if conn is None and needs_database:
            st.info(NO_RUNS)
            st.stop()
        yield Ctx(conn, cfg)


def flash(ok: bool, message: str) -> None:
    """Show ``message`` at the top of the next render (after a write and a rerun)."""
    st.session_state[FLASH_KEY] = (ok, message)


def light_banner(light: Light | None, extra: str = "") -> None:
    if light is None:
        st.markdown("**No status yet.**")
        return
    colour = LIGHT_COLOURS[light]
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:1rem">'
        f'<div style="width:4.5rem;height:4.5rem;border-radius:50%;background:{colour}"></div>'
        f'<div style="font-size:1.6rem;font-weight:600">{light.upper()}: {LIGHT_WORDS[light]}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )
    if extra:
        st.caption(extra)


def money(value: Decimal) -> str:
    return f"GBP {value:,.2f}"


def pct(value: float | None, digits: int = 1) -> str:
    return "-" if value is None else f"{value * 100:.{digits}f}%"


def age_text(age: timedelta | None) -> str:
    if age is None:
        return "never"
    seconds = max(0, int(age.total_seconds()))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    if days:
        return f"{days} d {hours} h ago"
    if hours:
        return f"{hours} h {rest // 60} min ago"
    return f"{rest // 60} min ago"


def data_status(verified: bool | None) -> str:
    return "verified" if verified else "unverified"


def allocation_rows(rows: tuple[AllocationLine, ...]) -> list[dict[str, str]]:
    return [
        {
            "Fund": r.symbol,
            "Kind": "Growth" if r.role == "growth" else "Safe fund",
            "Value": money(r.value),
            "Share of portfolio": pct(r.weight),
            "Target share": pct(r.target_weight),
            "Difference": "-" if r.drift is None else f"{r.drift * 100:+.1f} points",
            "Fund data": data_status(r.verified),
        }
        for r in rows
    ]


def gauge_fraction(signal: SignalView) -> float | None:
    """How far a signal is towards its trigger, 0 to 1, for the signals that have a size."""
    if signal.value is None or signal.trigger_level in (None, 0):
        return None
    assert signal.trigger_level is not None
    return max(0.0, min(1.0, signal.value / signal.trigger_level))


def show_signal(signal: SignalView) -> None:
    value = "no data" if signal.value is None else pct(signal.value)
    state = "TRIGGERED" if signal.negative else "ok"
    st.metric(signal.label, value, delta=state, delta_color="inverse" if signal.negative else "off")
    frac = gauge_fraction(signal)
    if frac is not None:
        st.progress(frac, text=f"{frac:.0%} of the way to the trigger")
    st.caption(signal.trigger_text)
