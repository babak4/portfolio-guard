"""Keep secrets out of log output.

Notification libraries log the URL they were given, and that URL holds the bot token. Call
``install_filter()`` once where logging is configured: it redacts every log record (message,
arguments and exception text) whatever logger or handler carries it, and quiets the chatty HTTP
and notification libraries to ERROR.
"""

from __future__ import annotations

import logging
import re
import traceback
from typing import Any

__all__ = ["QUIET_LOGGERS", "RedactingFilter", "install_filter", "redact"]

QUIET_LOGGERS = ("apprise", "urllib3", "requests", "httpx")
_MARK = "[REDACTED]"
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:tgram|ntfys?|tgrams)://\S+", re.IGNORECASE), "<notifier-url>" + _MARK),
    (re.compile(r"bot\d+:[A-Za-z0-9_-]+"), "bot" + _MARK),
    (re.compile(r"(?<=://)[^/\s@]+@"), _MARK + "@"),
    (
        re.compile(
            r"(?i)([?&](?:token|key|apikey|api_key|secret|password|passwd|auth|access_token)=)"
            r"[^&\s]+"
        ),
        r"\1" + _MARK,
    ),
)
_installed = "_guard_log_safety"


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a bad format string must not break logging
            message = str(record.msg)
        record.msg = redact(message)
        record.args = None
        if record.exc_info and record.exc_info[0] is not None:
            text = "".join(traceback.format_exception(*record.exc_info))
            record.exc_text = redact(text.rstrip("\n"))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


def install_filter() -> None:
    """Redact secrets in every log record from now on and quiet noisy libraries. Idempotent."""
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.ERROR)
    previous: Any = logging.getLogRecordFactory()
    if getattr(previous, _installed, False):
        return
    redactor = RedactingFilter()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record: logging.LogRecord = previous(*args, **kwargs)
        redactor.filter(record)
        return record

    setattr(factory, _installed, True)
    logging.setLogRecordFactory(factory)
