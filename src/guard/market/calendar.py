"""A small UK and US exchange holiday calendar, enough to compute the expected last close.

UK: England and Wales bank holidays. US: NYSE full-day closures. One-off closures (royal events,
national days of mourning) are not listed.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from functools import cache


def easter(year: int) -> date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = divmod(h + ell - m * 7 + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _next_free_weekday(d: date, taken: set[date]) -> date:
    while d.weekday() >= 5 or d in taken:
        d += timedelta(days=1)
    return d


@cache
def uk_holidays(year: int) -> frozenset[date]:
    taken: set[date] = set()
    for d in (date(year, 1, 1), date(year, 12, 25), date(year, 12, 26)):
        taken.add(_next_free_weekday(d, taken))
    e = easter(year)
    taken |= {
        e - timedelta(days=2),
        e + timedelta(days=1),
        _nth_weekday(year, 5, 0, 1),
        _last_weekday(year, 5, 0),
        _last_weekday(year, 8, 0),
    }
    return frozenset(taken)


@cache
def us_holidays(year: int) -> frozenset[date]:
    def observed(d: date) -> date:
        return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=d.weekday() == 6)

    days = {
        e
        for e in (
            _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
            _nth_weekday(year, 2, 0, 3),  # Presidents Day
            easter(year) - timedelta(days=2),
            _last_weekday(year, 5, 0),  # Memorial Day
            _nth_weekday(year, 9, 0, 1),  # Labor Day
            _nth_weekday(year, 11, 3, 4),  # Thanksgiving
            observed(date(year, 7, 4)),
            observed(date(year, 12, 25)),
        )
    }
    if year >= 2022:
        days.add(observed(date(year, 6, 19)))
    new_year = date(year, 1, 1)
    if new_year.weekday() != 5:  # a Saturday New Year is not observed on the Friday
        days.add(observed(new_year))
    return frozenset(days)


_BY_MARKET = {"uk": uk_holidays, "us": us_holidays}


def is_trading_day(d: date, markets: Sequence[str] = ("uk", "us")) -> bool:
    """True when ``d`` is a weekday and every listed market is open."""
    if d.weekday() >= 5:
        return False
    return all(d not in _BY_MARKET[m](d.year) for m in markets)


def last_trading_day_before(as_of: date, markets: Sequence[str] = ("uk", "us")) -> date:
    """The most recent trading day strictly before ``as_of`` (the latest completed close)."""
    d = as_of - timedelta(days=1)
    while not is_trading_day(d, markets):
        d -= timedelta(days=1)
    return d
