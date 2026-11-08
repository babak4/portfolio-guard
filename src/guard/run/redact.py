"""Strip amounts from any text that can leave the process (logs, stored reasons, notices).

Ingestion errors quote totals and gaps. Text that reaches a log, the database, an alert or the
terminal must not carry them, so every digit run is replaced; ISO dates and short digit suffixes
on a capitalised name (``CSH2``) are kept because they identify a day or a fund, not a balance.
Digits glued to a currency code or a lower-case word (``GBP12000``, ``balance12,345.67``) are
amounts and are masked.
"""

from __future__ import annotations

import re

_ISO_DATE = r"\d{4}-\d{2}-\d{2}"
_NUMBER = r"[£$€]?(?<![A-Za-z\d])\d[\d,]*(?:\.\d+)?"
_GLUED = r"(?<=[A-Za-z])\d+(?:,\d{3})*(?:\.\d+)?"
_PATTERN = re.compile(f"(?P<date>{_ISO_DATE})|(?P<free>{_NUMBER})|(?P<glued>{_GLUED})")
_WORD_BEFORE = re.compile(r"[A-Za-z]+$")
_CURRENCY_CODES = frozenset({"GBP", "USD", "EUR", "CHF", "JPY", "CAD", "AUD"})
_SHORT_SUFFIX = 2


def _replace(m: re.Match[str]) -> str:
    if m.group("date"):
        return m.group("date")
    if m.group("free"):
        return "#"
    word = _WORD_BEFORE.search(m.string[: m.start()])
    prefix = word.group(0) if word else ""
    digits = m.group("glued")
    is_fund_name = (
        prefix.isupper()
        and prefix not in _CURRENCY_CODES
        and len(digits) <= _SHORT_SUFFIX
        and digits.isdigit()
    )
    return digits if is_fund_name else "#"


def redact_amounts(text: str) -> str:
    """``text`` with every number (and any currency sign on it) replaced by ``#``."""
    return _PATTERN.sub(_replace, text)
