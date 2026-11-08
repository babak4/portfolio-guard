from __future__ import annotations

from dataclasses import replace

from guard.advice.constraints import check_fees, check_unknown, check_us_only

from .helpers import holding, registry


def test_us_only_52_percent_warns_and_names_funds() -> None:
    hs = [holding("isa", "VUAG", 520), holding("isa", "VWRP", 480)]
    flag = check_us_only(hs, registry(), 0.5)
    assert flag is not None and flag.severity == "warn"
    assert flag.symbols == ("VUAG",)
    assert "52.0%" in flag.message and "VUAG" in flag.message


def test_us_only_exactly_cap_does_not_warn() -> None:
    hs = [holding("isa", "VUAG", 500), holding("isa", "VWRP", 500)]
    flag = check_us_only(hs, registry(), 0.5)
    assert flag is not None and flag.severity == "info"
    assert "50.0%" in flag.message


def test_global_fund_does_not_count() -> None:
    hs = [holding("isa", "VWRP", 900), holding("isa", "VUAG", 100)]
    flag = check_us_only(hs, registry(), 0.5)
    assert flag is not None and flag.severity == "info" and "10.0%" in flag.message


def test_brkb_counts_toward_us_share() -> None:
    hs = [
        holding("gia", "BRK.B", 300, excluded=True),
        holding("isa", "VUAG", 250),
        holding("isa", "VWRP", 450),
    ]
    flag = check_us_only(hs, registry(), 0.5)
    assert flag is not None and flag.severity == "warn"
    assert set(flag.symbols) == {"BRK.B", "VUAG"}


def test_us_only_empty_portfolio() -> None:
    assert check_us_only([], registry(), 0.5) is None


def reg_with(**changes: dict[str, object]):  # type: ignore[no-untyped-def]
    reg = registry()
    funds = dict(reg.by_symbol)
    for symbol, fields in changes.items():
        funds[symbol] = replace(funds[symbol], **fields)
    return type(reg)(funds)


def test_fee_above_ceiling_suggests_available_equivalent_alternative() -> None:
    # VWRP at 0.25% held; VWCE (0.19%) tracks the same index and is in the instruments list
    reg = reg_with(VWRP={"ongoing_charge": 0.0025})
    hs = [holding("isa", "VWRP", 1000)]
    flags = check_fees(hs, reg, 0.0015, frozenset({"VWCE", "VUAG"}))
    warn = [f for f in flags if f.severity == "warn"]
    assert len(warn) == 1
    assert "VWRP" in warn[0].message and "0.25%" in warn[0].message
    assert "0.15%" in warn[0].message and "VWCE" in warn[0].message
    assert "VUAG" not in warn[0].message
    assert "VWCE" in warn[0].symbols


def test_other_index_is_never_offered_as_a_cheaper_alternative() -> None:
    hs = [holding("isa", "XDEW", 1000)]
    w = next(
        f for f in check_fees(hs, registry(), 0.0015, frozenset({"VUAG"})) if f.severity == "warn"
    )
    assert "VUAG" not in w.message and "no cheaper" in w.message


def test_alternative_availability_is_unchanged() -> None:
    reg = reg_with(VWRP={"ongoing_charge": 0.0025})
    hs = [holding("isa", "VWRP", 1000)]
    w = next(
        f for f in check_fees(hs, reg, 0.0015, frozenset({"SOMETHING"})) if f.severity == "warn"
    )
    assert "VWCE" not in w.message and "no cheaper" in w.message
    reg = reg_with(VWRP={"ongoing_charge": 0.0025}, VWCE={"available_on_t212": True})
    w = next(f for f in check_fees(hs, reg, 0.0015, None) if f.severity == "warn")
    assert "VWCE" in w.message
    w = next(
        f
        for f in check_fees(hs, reg_with(VWRP={"ongoing_charge": 0.0025}), 0.0015, None)
        if f.severity == "warn"
    )
    assert "VWCE" not in w.message


def test_unverified_figures_are_labelled_in_fee_messages() -> None:
    reg = reg_with(VWRP={"ongoing_charge": 0.0025})
    hs = [holding("isa", "VWRP", 1000)]
    w = next(f for f in check_fees(hs, reg, 0.0015, frozenset({"VWCE"})) if f.severity == "warn")
    assert w.message.count("(unverified figure)") == 2  # the held fund and the alternative
    verified = reg_with(VWRP={"ongoing_charge": 0.0025, "verified": True}, VWCE={"verified": True})
    w = next(
        f for f in check_fees(hs, verified, 0.0015, frozenset({"VWCE"})) if f.severity == "warn"
    )
    assert "unverified" not in w.message


def test_null_charge_is_never_presented_as_cheap_or_expensive() -> None:
    reg = reg_with(VWRP={"ongoing_charge": 0.0025}, VWCE={"ongoing_charge": None})
    hs = [holding("isa", "VWRP", 1000)]
    w = next(f for f in check_fees(hs, reg, 0.0015, frozenset({"VWCE"})) if f.severity == "warn")
    assert "VWCE" not in w.message
    # a held fund with a null charge is an info line that makes no claim about cost
    info = check_fees([holding("isa", "VWCE", 1000)], reg, 0.0015, frozenset({"VWRP"}))
    assert [f.severity for f in info] == ["info"]
    assert "above" not in info[0].message and "cheaper" not in info[0].message


def test_safe_asset_options_may_differ_and_say_so() -> None:
    reg = reg_with(
        CSH2={"ongoing_charge": 0.0020, "index_group": "gbp-money-market"},
        XSTR={"index_group": "other-money-market"},
    )
    flags = check_fees([holding("isa", "CSH2", 500)], reg, 0.0015, frozenset({"XSTR"}))
    w = next(f for f in flags if f.severity == "warn")
    assert "XSTR" in w.message and "your choice" in w.message
    assert "differ" in w.message
    assert "(unverified figure)" in w.message or "fee unknown" in w.message


def test_fee_unknown_is_info() -> None:
    flags = check_fees([holding("isa", "VHVG", 500)], registry(), 0.0015, None)
    assert [f.severity for f in flags] == ["info"]
    assert "fee unknown" in flags[0].message and "VHVG" in flags[0].message


def test_fee_within_ceiling_no_flag_and_satellite_skipped() -> None:
    hs = [holding("isa", "VUAG", 500), holding("gia", "BRK.B", 500, excluded=True)]
    assert check_fees(hs, registry(), 0.0015, None) == ()


def test_safe_asset_options_listed_when_above_ceiling() -> None:
    reg = registry()
    csh2 = replace(reg.get("CSH2"), ongoing_charge=0.0020)  # type: ignore[type-var]
    reg2 = type(reg)({**reg.by_symbol, "CSH2": csh2})
    flags = check_fees([holding("isa", "CSH2", 500)], reg2, 0.0015, frozenset({"XSTR"}))
    w = next(f for f in flags if f.severity == "warn")
    assert "XSTR" in w.message and "fee unknown" in w.message and "your choice" in w.message


def test_unknown_fund_flagged() -> None:
    flags = check_unknown([holding("isa", "ZZZZ", 100), holding("isa", "VWRP", 100)], registry())
    assert len(flags) == 1
    assert flags[0].severity == "warn"
    assert flags[0].message == (
        "unknown fund ZZZZ: add it to the funds file; it is not covered by the US and fee checks"
    )
