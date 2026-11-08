"""Strip amounts from any text that can leave the process (logs, stored reasons, notices).

Ingestion errors quote totals and gaps. Text that reaches a log, the database, an alert or the
terminal must not carry them, so every digit run is replaced; ISO dates and digits inside a name
(``CSH2``) are kept because they identify a day or a fund, not a balance.
"""

from __future__ import annotations

import re

_ISO_DATE = r"\d{4}-\d{2}-\d{2}"
_NUMBER = r"[£$€]?(?<![A-Za-z\d])\d[\d,]*(?:\.\d+)?"
_PATTERN = re.compile(f"({_ISO_DATE})|{_NUMBER}")


def redact_amounts(text: str) -> str:
    """``text`` with every number (and any currency sign on it) replaced by ``#``."""
    return _PATTERN.sub(lambda m: m.group(1) or "#", text)
