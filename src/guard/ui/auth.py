"""Who may see the dashboard.

Three modes. With both a list of allowed Tailscale logins and a password set, both are required
(the login header can be forged by anyone who reaches the app directly, so the password backs it
up; this is the recommended setup). With only the list, the header alone decides and the page
shows a notice that access relies on ``tailscale serve`` being the only way in. With only a
password, the password decides. Otherwise access is refused.
``GUARD_DASHBOARD_INSECURE_LOCAL=1`` opens it for local development, and only while the server
address is explicitly loopback. ``decide`` is pure; ``require_access`` applies it inside a
Streamlit page.
"""

from __future__ import annotations

import hmac
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

USERS_ENV = "GUARD_ALLOWED_USERS"
PASSWORD_ENV = "GUARD_DASHBOARD_PASSWORD"
INSECURE_ENV = "GUARD_DASHBOARD_INSECURE_LOCAL"
USER_HEADER = "Tailscale-User-Login"
SESSION_KEY = "guard_authenticated"
MIN_PASSWORD_LENGTH = 12
MAX_FAILURES = 10
MAX_DELAY_SECONDS = 30.0
LOCK_SECONDS = 900.0
USERS_ONLY_NOTICE = (
    "Access relies on tailscale serve being the only way in. Set "
    f"{PASSWORD_ENV} as well so a forged login header alone cannot open the dashboard."
)
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
    notice: str = ""


class LoginThrottle:
    """Per-process password-failure counter: growing delay (capped), then a temporary lock."""

    def __init__(self) -> None:
        self.failures = 0
        self._last = 0.0
        self._locked_until = 0.0

    def record_failure(self, now: float) -> None:
        self.failures += 1
        self._last = now
        if self.failures >= MAX_FAILURES:
            self._locked_until = now + LOCK_SECONDS
            self.failures = 0

    def record_success(self) -> None:
        self.failures = 0
        self._locked_until = 0.0

    def locked(self, now: float) -> bool:
        return now < self._locked_until

    def wait_seconds(self, now: float) -> float:
        if self.failures == 0:
            return 0.0
        delay = min(MAX_DELAY_SECONDS, float(2 ** (self.failures - 1)))
        return max(0.0, self._last + delay - now)


THROTTLE = LoginThrottle()


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
    password = env.get(PASSWORD_ENV, "")
    if password and len(password) < MIN_PASSWORD_LENGTH:
        return Decision(
            "refused",
            f"{PASSWORD_ENV} must be at least {MIN_PASSWORD_LENGTH} characters long.",
        )
    if users_raw:
        allowed = {u.strip().lower() for u in users_raw.split(",") if u.strip()}
        who = (_header(headers, USER_HEADER) or "").strip().lower()
        if not who or who not in allowed:
            return Decision("denied", "This account is not allowed to see the dashboard.")
        if password:
            return Decision("allowed") if authenticated else Decision("need_password")
        return Decision("allowed", notice=USERS_ONLY_NOTICE)
    if password:
        return Decision("allowed") if authenticated else Decision("need_password")
    if env.get(INSECURE_ENV) == "1":
        if server_address in _LOOPBACK:
            return Decision("allowed")
        return Decision(
            "refused",
            f"{INSECURE_ENV}=1 is only for loopback; the server address is "
            f"{server_address or 'not set (it would listen on every interface)'}.",
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
        if decision.notice:
            st.warning(decision.notice)
        return
    if decision.state == "need_password":
        st.title("Portfolio Guard")
        with st.form("login"):
            supplied = st.text_input("Dashboard password", type="password")
            if st.form_submit_button("Sign in"):
                now = time.monotonic()
                if THROTTLE.locked(now):
                    st.error("Too many wrong passwords. Sign-in is locked for a while.")
                elif (wait := THROTTLE.wait_seconds(now)) > 0:
                    st.error(f"Wait {wait:.0f} seconds before trying again.")
                elif password_ok(supplied, os.environ):
                    THROTTLE.record_success()
                    state[SESSION_KEY] = True
                    st.rerun()
                else:
                    THROTTLE.record_failure(now)
                    st.error("That password is not right.")
        st.stop()
    st.error(decision.message)
    st.stop()
