from __future__ import annotations

from guard.run.redact import redact_amounts


def test_numbers_and_currency_amounts_become_a_hash() -> None:
    text = (
        "positions plus cash differ from the account total by 1234.56 (total 99,999.00) of £5,000"
    )
    out = redact_amounts(text)
    assert out == "positions plus cash differ from the account total by # (total #) of #"
    assert not any(ch.isdigit() for ch in out)


def test_iso_dates_and_fund_names_survive() -> None:
    assert redact_amounts("last close 2026-11-03 for CSH2 is 4 days behind") == (
        "last close 2026-11-03 for CSH2 is # days behind"
    )


def test_negative_numbers_and_percentages_lose_their_digits() -> None:
    assert redact_amounts("moved -12.5% to 0.5%") == "moved -#% to #%"
