from __future__ import annotations

import io
import logging
from collections.abc import Iterator

import pytest

from guard.report import log_safety

TOKEN = "bot123456789:AAE-secret_token-value"


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    root = logging.getLogger()
    saved_handlers, saved_filters = root.handlers[:], root.filters[:]
    saved_factory = logging.getLogRecordFactory()
    saved_levels = {n: logging.getLogger(n).level for n in log_safety.QUIET_LOGGERS}
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    root.handlers = [handler]
    root.filters = []
    yield buf
    root.handlers, root.filters = saved_handlers, saved_filters
    logging.setLogRecordFactory(saved_factory)
    for name, level in saved_levels.items():
        logging.getLogger(name).setLevel(level)


@pytest.mark.parametrize(
    "text",
    [
        f"Failed to send https://api.telegram.org/{TOKEN}/sendMessage",
        "Notifying tgram://123456789:AAE-secret_token-value/55555",
        "posting to ntfy://user:pw@ntfy.example.com/topic-secret",
        "https://hooks.example.com/services/send?token=abc123&x=1",
    ],
)
def test_redact_removes_secret_parts(text: str) -> None:
    out = log_safety.redact(text)
    for secret in ("AAE-secret", "123456789:", "pw@", "topic-secret", "abc123"):
        assert secret not in out
    assert "REDACTED" in out


def test_innocent_text_is_unchanged() -> None:
    msg = "weekly run ok for slot 2026-11-09"
    assert log_safety.redact(msg) == msg


def test_filter_redacts_message_and_args_on_the_root_logger(stream: io.StringIO) -> None:
    log_safety.install_filter()
    logging.getLogger("apprise.plugins").error(
        "Telegram send failed: %s", f"https://api.telegram.org/{TOKEN}/sendMessage"
    )
    logging.getLogger("guard.worker").warning("tgram://123456789:AAE-secret_token-value/1 failed")
    text = stream.getvalue()
    assert "AAE-secret" not in text and "123456789:" not in text
    assert "REDACTED" in text


def test_exception_text_is_redacted_too(stream: io.StringIO) -> None:
    log_safety.install_filter()
    try:
        raise RuntimeError(f"boom https://api.telegram.org/{TOKEN}/sendMessage")
    except RuntimeError:
        logging.getLogger("guard").exception("send failed")
    assert "AAE-secret" not in stream.getvalue()


def test_noisy_libraries_are_quieted_and_install_is_idempotent(stream: io.StringIO) -> None:
    log_safety.install_filter()
    log_safety.install_filter()
    for name in ("apprise", "urllib3", "requests", "httpx"):
        assert logging.getLogger(name).level == logging.ERROR
    logging.getLogger("guard").error("once %s", TOKEN)
    assert stream.getvalue().count("REDACTED") == 1
