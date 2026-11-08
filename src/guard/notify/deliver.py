"""Send a message through Apprise, with a fallback channel, and record every attempt.

Tokens, topics and URLs live only in ``Channel`` objects built from secrets. They never reach a
log line or a stored error: errors are reduced to the exception type plus text with the
channel's secrets, URLs and numbers removed.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlparse

import apprise

from guard.notify.heartbeat import heartbeat_key
from guard.notify.render import Message, render_notice
from guard.run.redact import redact_amounts
from guard.run.results import Notice
from guard.secrets import SecretNotFound, get_secret
from guard.storage import open_ops
from guard.storage.alert_store import AlertStore

if TYPE_CHECKING:
    from guard.run.deps import Deps

log = logging.getLogger("guard.notify")

NOT_CONFIGURED = "not configured"
_URL = re.compile(r"\S+://\S+")
_BOT_TOKEN = re.compile(r"\d+:[A-Za-z0-9_-]{20,}")
_NOTIFY_TYPE = {
    "info": apprise.NotifyType.INFO,
    "warn": apprise.NotifyType.WARNING,
    "urgent": apprise.NotifyType.FAILURE,
}


class DeliveryError(RuntimeError):
    """A channel did not accept the message. The text never contains a URL or token."""


@dataclass(frozen=True)
class Channel:
    name: str  # telegram | ntfy
    url: str = field(repr=False)
    secrets: tuple[str, ...] = field(default=(), repr=False)


class Sender(Protocol):
    def send(self, channel: Channel, message: Message) -> None:
        """Deliver ``message`` or raise."""


class AppriseSender:
    def send(self, channel: Channel, message: Message) -> None:
        app = apprise.Apprise()
        if not app.add(channel.url):
            raise DeliveryError(f"{channel.name}: the channel settings are not valid")
        ok = app.notify(
            title=message.title, body=message.body, notify_type=_NOTIFY_TYPE[message.urgency]
        )
        if not ok:
            raise DeliveryError(f"{channel.name}: the service did not accept the message")


@dataclass(frozen=True)
class Attempt:
    channel: str
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    channel: str | None
    attempts: tuple[Attempt, ...]


def _optional(name: str) -> str | None:
    try:
        value = get_secret(name).strip()
    except SecretNotFound:
        return None
    return value or None


def channels_from_secrets() -> list[Channel]:
    """The configured channels in order: Telegram first, then the ntfy fallback."""
    out: list[Channel] = []
    token = _optional("guard_telegram_bot_token")
    chat = _optional("guard_telegram_chat_id")
    if token and chat:
        out.append(Channel("telegram", f"tgram://{token}/{chat}", (token, chat)))
    topic = _optional("guard_ntfy_topic")
    if topic:
        server = _optional("guard_ntfy_server")
        if server is None:
            url = f"ntfy://{topic}"  # the public ntfy.sh server
        else:
            parsed = urlparse(server if "://" in server else f"https://{server}")
            scheme = "ntfys" if parsed.scheme == "https" else "ntfy"
            url = f"{scheme}://{parsed.netloc or parsed.path}/{topic}"
        out.append(Channel("ntfy", url, (topic,)))
    return out


def safe_error(exc: BaseException, channel: Channel) -> str:
    """The exception type and a message with secrets, URLs and amounts removed."""
    text = str(exc)
    for secret in sorted(channel.secrets, key=len, reverse=True):
        if secret:
            text = text.replace(secret, "[secret]")
    text = _URL.sub("[url]", text)
    text = _BOT_TOKEN.sub("[secret]", text)
    return f"{type(exc).__name__}: {redact_amounts(text)}"[:300]


def deliver(
    message: Message,
    sender: Sender,
    channels: Sequence[Channel],
    store: AlertStore,
    now: datetime,
) -> DeliveryResult:
    """Try each channel in order until one accepts the message. Never raises."""

    def record(channel: str, ok: bool, error: str | None) -> None:
        try:
            store.record(
                kind=message.kind,
                urgency=message.urgency,
                channel=channel,
                ok=ok,
                error=error,
                now=now,
            )
        except Exception as e:  # noqa: BLE001 - recording must not break the run
            log.error("could not record an alert attempt: %s", type(e).__name__)

    if not channels:
        log.warning("alert not sent: no channel is configured")
        record("none", False, NOT_CONFIGURED)
        return DeliveryResult(False, None, (Attempt("none", False, NOT_CONFIGURED),))
    attempts: list[Attempt] = []
    for channel in channels:
        try:
            sender.send(channel, message)
        except Exception as e:  # noqa: BLE001 - any failure falls through to the next channel
            error = safe_error(e, channel)
            log.warning("alert via %s failed: %s", channel.name, error)
            record(channel.name, False, error)
            attempts.append(Attempt(channel.name, False, error))
            continue
        record(channel.name, True, None)
        attempts.append(Attempt(channel.name, True))
        return DeliveryResult(True, channel.name, tuple(attempts))
    log.error("alert could not be delivered on any channel")
    return DeliveryResult(False, None, tuple(attempts))


def _record_static(conn: sqlite3.Connection | None, kind: str, ok: bool, error: str | None) -> None:
    """Best effort: note a static push in the alert log, opening the database if none is given."""
    own = conn is None
    try:
        target = open_ops() if conn is None else conn
    except Exception as e:  # noqa: BLE001 - the push matters more than its record
        log.error("could not record a static alert: %s", type(e).__name__)
        return
    try:
        AlertStore(target).record(
            kind=kind, urgency="urgent", channel="static", ok=ok, error=error, now=datetime.now(UTC)
        )
    except Exception as e:  # noqa: BLE001
        log.error("could not record a static alert: %s", type(e).__name__)
    finally:
        if own:
            target.close()


def push_static(
    title: str,
    body: str,
    *,
    kind: str,
    sender: Sender | None = None,
    channels: Callable[[], Sequence[Channel]] = channels_from_secrets,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Push fixed text straight from the secrets, with no ``Deps``, rendering or run data.

    The last line of defence when the normal alert path cannot start or has itself failed. It
    never raises and logs only exception types. ``True`` when a channel accepted the message."""
    try:
        chosen: Sender = sender or AppriseSender()
        message = Message(title, body, "urgent", kind)
        found = list(channels())
    except Exception as e:  # noqa: BLE001 - nothing here may raise
        log.error("static alert could not be prepared: %s", type(e).__name__)
        return False
    if not found:
        log.error("static alert not sent: no channel is configured")
    for channel in found:
        try:
            chosen.send(channel, message)
        except Exception as e:  # noqa: BLE001 - try the next channel
            log.error("static alert via %s failed: %s", channel.name, type(e).__name__)
            continue
        _record_static(conn, kind, True, None)
        return True
    _record_static(conn, kind, False, NOT_CONFIGURED if not found else "all channels failed")
    return False


PIPELINE_FAILED_TITLE = "Portfolio Guard: alert pipeline failed"
PIPELINE_FAILED_BODY = (
    "An alert could not be prepared or sent normally. Check the dashboard and the worker log."
)


def make_notifier(
    deps: Deps,
    *,
    sender: Sender | None = None,
    channels: Callable[[], Sequence[Channel]] = channels_from_secrets,
) -> Callable[[Notice], None]:
    """The function for ``Deps.notify``: render the notice and deliver it. It never raises; if the
    pipeline itself breaks it still tries a fixed-text push."""
    chosen: Sender = sender or AppriseSender()

    def notify(notice: Notice) -> None:
        try:
            _send(notice)
        except Exception as e:  # noqa: BLE001 - an alert problem must never break a run
            log.error("could not send a %s notice: %s", notice.status, type(e).__name__)
            push_static(
                PIPELINE_FAILED_TITLE,
                PIPELINE_FAILED_BODY,
                kind="pipeline",
                sender=chosen,
                channels=channels,
                conn=deps.ops,
            )

    def _send(notice: Notice) -> None:
        accounts = [a.id for a in deps.config.portfolio.accounts]
        message = render_notice(notice, accounts)
        store = AlertStore(deps.ops)
        now = deps.clock()
        once_key = _once_key(notice)
        if once_key is not None and notice.status == "event" and store.once_done(once_key):
            return
        result = deliver(message, chosen, channels(), store, now)
        if result.delivered and once_key is not None:
            store.mark_once(once_key, now)

    return notify


def _once_key(notice: Notice) -> str | None:
    """What makes a notice a repeat: a missing-week alert per slot, a daily event per slot and
    kind. Only these are sent once; reports and failures are sent every time."""
    if notice.status == "missing":
        return heartbeat_key(notice.slot)
    if notice.status == "event":
        kind = notice.event.kind if notice.event is not None else "unknown"
        return f"daily:{notice.slot}:{kind}"
    return None
