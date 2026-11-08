from __future__ import annotations

from guard.ui import auth

USERS = {"GUARD_ALLOWED_USERS": "me@example.com, Other@Example.com"}
PASSWORD = {"GUARD_DASHBOARD_PASSWORD": "s3cret"}
LOCAL = {"GUARD_DASHBOARD_INSECURE_LOCAL": "1"}


def test_allowed_user_header_is_accepted_case_insensitively() -> None:
    d = auth.decide(USERS, {"Tailscale-User-Login": "OTHER@example.com"}, authenticated=False)
    assert d.state == "allowed"


def test_header_name_is_case_insensitive() -> None:
    d = auth.decide(USERS, {"tailscale-user-login": "me@example.com"}, authenticated=False)
    assert d.state == "allowed"


def test_unlisted_or_missing_user_is_denied() -> None:
    assert (
        auth.decide(USERS, {"Tailscale-User-Login": "x@y.z"}, authenticated=False).state == "denied"
    )
    assert auth.decide(USERS, {}, authenticated=False).state == "denied"


def test_allowed_users_win_over_a_password() -> None:
    d = auth.decide(USERS | PASSWORD, {}, authenticated=True)
    assert d.state == "denied"  # a password does not open a tailnet-only dashboard


def test_password_mode_asks_then_allows_once_authenticated() -> None:
    assert auth.decide(PASSWORD, {}, authenticated=False).state == "need_password"
    assert auth.decide(PASSWORD, {}, authenticated=True).state == "allowed"


def test_password_check_is_exact_and_constant_time_api() -> None:
    assert auth.password_ok("s3cret", PASSWORD)
    assert not auth.password_ok("s3cre", PASSWORD)
    assert not auth.password_ok("", PASSWORD)
    assert not auth.password_ok("anything", {})  # no password configured: nothing matches


def test_nothing_configured_refuses_with_a_message() -> None:
    d = auth.decide({}, {}, authenticated=False)
    assert d.state == "refused" and "GUARD_ALLOWED_USERS" in d.message


def test_insecure_local_allows_on_loopback_only() -> None:
    assert (
        auth.decide(LOCAL, {}, authenticated=False, server_address="127.0.0.1").state == "allowed"
    )
    assert auth.decide(LOCAL, {}, authenticated=False, server_address=None).state == "allowed"
    refused = auth.decide(LOCAL, {}, authenticated=False, server_address="0.0.0.0")
    assert refused.state == "refused" and "loopback" in refused.message


def test_insecure_flag_must_be_exactly_one() -> None:
    off = {"GUARD_DASHBOARD_INSECURE_LOCAL": "true"}
    assert auth.decide(off, {}, authenticated=False).state == "refused"
