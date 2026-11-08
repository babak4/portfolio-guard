"""Who may see the dashboard.

Order of precedence: a list of allowed Tailscale logins, else a shared password, else refuse.
``GUARD_DASHBOARD_INSECURE_LOCAL=1`` opens it for local development, and only while the server
listens on loopback. ``decide`` is pure; ``require_access`` applies it inside a Streamlit page.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

USERS_ENV = "GUARD_ALLOWED_USERS"
PASSWORD_ENV = "GUARD_DASHBOARD_PASSWORD"
INSECURE_ENV = "GUARD_DASHBOARD_INSECURE_LOCAL"
USER_HEADER = "Tailscale-User-Login"
SESSION_KEY = "guard_authenticated"
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})

State = Literal["allowed", "need_password", "denied", "refused"]

NOT_CONFIGURED = (
    f"The dashboard is not configured for access. Set {USERS_ENV} (comma-separated Tailscale "
    f"logins) or {PASSWORD_ENV}. For local development on this machine only, set {INSECURE_ENV}=1."
)


@dataclass(frozen=True)
class Decision:
    state: State
    message: str = ""


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def password_ok(supplied: str, env: Mapping[str, str]) -> bool:
    expected = env.get(PASSWORD_ENV, "")
    if not expected or not supplied:
        return False
    return hmac.compare_digest(supplied.encode(), expected.encode())


def decide(
    env: Mapping[str, str],
    headers: Mapping[str, str],
    *,
    authenticated: bool,
    server_address: str | None = None,
) -> Decision:
    users_raw = env.get(USERS_ENV, "").strip()
    if users_raw:
        allowed = {u.strip().lower() for u in users_raw.split(",") if u.strip()}
        who = (_header(headers, USER_HEADER) or "").strip().lower()
        if who and who in allowed:
            return Decision("allowed")
        return Decision("denied", "This account is not allowed to see the dashboard.")
    if env.get(PASSWORD_ENV):
        return Decision("allowed") if authenticated else Decision("need_password")
    if env.get(INSECURE_ENV) == "1":
        if server_address in (None, "") or server_address in _LOOPBACK:
            return Decision("allowed")
        return Decision(
            "refused",
            f"{INSECURE_ENV}=1 is only for loopback; the server listens on {server_address}.",
        )
    return Decision("refused", NOT_CONFIGURED)


def require_access() -> None:
    """Show the page only to someone allowed; otherwise explain and stop the script."""
    import streamlit as st

    state = st.session_state
    decision = decide(
        os.environ,
        dict(st.context.headers),
        authenticated=bool(state.get(SESSION_KEY)),
        server_address=st.get_option("server.address"),
    )
    if decision.state == "allowed":
        return
    if decision.state == "need_password":
        st.title("Portfolio Guard")
        with st.form("login"):
            supplied = st.text_input("Dashboard password", type="password")
            if st.form_submit_button("Sign in"):
                if password_ok(supplied, os.environ):
                    state[SESSION_KEY] = True
                    st.rerun()
                st.error("That password is not right.")
        st.stop()
    st.error(decision.message)
    st.stop()
