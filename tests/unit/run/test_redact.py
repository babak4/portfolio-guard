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


def test_digits_glued_to_a_currency_code_or_a_word_are_masked() -> None:
    assert redact_amounts("total GBP12000 left") == "total GBP# left"
    assert redact_amounts("balance12,345.67 now") == "balance# now"
    assert redact_amounts("USD5000.50") == "USD#"


def test_glued_digits_in_a_fund_name_survive_and_dates_next_to_words_too() -> None:
    assert redact_amounts("CSH2, VWRP and V3AB") == "CSH2, VWRP and V3AB"
    assert redact_amounts("slot2026-11-09 done") == "slot2026-11-09 done"
