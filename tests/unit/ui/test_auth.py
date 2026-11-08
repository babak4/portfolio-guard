from __future__ import annotations

from guard.ui import auth

USERS = {"GUARD_ALLOWED_USERS": "me@example.com, Other@Example.com"}
PASSWORD = {"GUARD_DASHBOARD_PASSWORD": "s3cret-and-long-enough"}
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


def test_users_and_password_both_required_when_both_set() -> None:
    hdr = {"Tailscale-User-Login": "me@example.com"}
    assert auth.decide(USERS | PASSWORD, {}, authenticated=True).state == "denied"
    assert auth.decide(USERS | PASSWORD, hdr, authenticated=False).state == "need_password"
    ok = auth.decide(USERS | PASSWORD, hdr, authenticated=True)
    assert ok.state == "allowed" and ok.notice == ""


def test_users_only_is_allowed_with_a_visible_notice() -> None:
    d = auth.decide(USERS, {"Tailscale-User-Login": "me@example.com"}, authenticated=False)
    assert d.state == "allowed"
    assert "tailscale serve being the only way in" in d.notice


def test_short_password_is_refused() -> None:
    short = {"GUARD_DASHBOARD_PASSWORD": "short-pw-11"}
    assert len("short-pw-11") == 11
    d = auth.decide(short, {}, authenticated=True)
    assert d.state == "refused" and "12" in d.message
    both = auth.decide(
        USERS | short, {"Tailscale-User-Login": "me@example.com"}, authenticated=True
    )
    assert both.state == "refused"
    ok = {"GUARD_DASHBOARD_PASSWORD": "twelve-chars"}
    assert auth.decide(ok, {}, authenticated=False).state == "need_password"


def test_throttle_delays_exponentially_caps_and_locks() -> None:
    t = auth.LoginThrottle()
    assert t.wait_seconds(0.0) == 0.0
    t.record_failure(0.0)
    assert t.wait_seconds(0.0) == 1.0
    assert t.wait_seconds(5.0) == 0.0
    t.record_failure(10.0)
    assert t.wait_seconds(10.0) == 2.0
    for _ in range(6):
        t.record_failure(100.0)  # 8 failures: delay would be 128 s, capped
    assert t.wait_seconds(100.0) == 30.0
    t.record_failure(100.0)
    assert not t.locked(100.0)
    t.record_failure(100.0)  # tenth failure
    assert t.locked(100.0)
    assert not t.locked(100.0 + auth.LOCK_SECONDS + 1)
    t.record_success()
    assert t.wait_seconds(100.0) == 0.0 and not t.locked(100.0)


def test_password_mode_asks_then_allows_once_authenticated() -> None:
    assert auth.decide(PASSWORD, {}, authenticated=False).state == "need_password"
    assert auth.decide(PASSWORD, {}, authenticated=True).state == "allowed"


def test_password_check_is_exact_and_constant_time_api() -> None:
    assert auth.password_ok("s3cret-and-long-enough", PASSWORD)
    assert not auth.password_ok("s3cret-and-long-enoug", PASSWORD)
    assert not auth.password_ok("", PASSWORD)
    assert not auth.password_ok("anything", {})  # no password configured: nothing matches


def test_nothing_configured_refuses_with_a_message() -> None:
    d = auth.decide({}, {}, authenticated=False)
    assert d.state == "refused" and "GUARD_ALLOWED_USERS" in d.message


def test_insecure_local_allows_on_loopback_only() -> None:
    assert (
        auth.decide(LOCAL, {}, authenticated=False, server_address="127.0.0.1").state == "allowed"
    )
    for local in ("localhost", "::1"):
        assert auth.decide(LOCAL, {}, authenticated=False, server_address=local).state == "allowed"
    refused = auth.decide(LOCAL, {}, authenticated=False, server_address="0.0.0.0")
    assert refused.state == "refused" and "loopback" in refused.message


def test_insecure_flag_must_be_exactly_one() -> None:
    off = {"GUARD_DASHBOARD_INSECURE_LOCAL": "true"}
    assert auth.decide(off, {}, authenticated=False).state == "refused"


def test_insecure_flag_refused_when_address_unknown() -> None:
    for addr in (None, ""):
        d = auth.decide(LOCAL, {}, authenticated=False, server_address=addr)
        assert d.state == "refused" and "loopback" in d.message
