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


def test_fee_above_ceiling_suggests_available_cheaper_alternative() -> None:
    # XDEW (0.20%) held, VUAG (0.07%) is cheaper and in the instruments list
    hs = [holding("isa", "XDEW", 1000)]
    flags = check_fees(hs, registry(), 0.0015, frozenset({"VUAG"}))
    warn = [f for f in flags if f.severity == "warn"]
    assert len(warn) == 1
    assert "XDEW" in warn[0].message and "0.20%" in warn[0].message
    assert "0.15%" in warn[0].message and "VUAG" in warn[0].message
    assert "VUAG" in warn[0].symbols


def test_fee_022_percent_with_alternative_missing_from_available_not_suggested() -> None:
    reg = registry()
    xdew = replace(reg.get("XDEW"), ongoing_charge=0.0022)  # type: ignore[type-var]
    reg2 = type(reg)({**reg.by_symbol, "XDEW": xdew})
    hs = [holding("isa", "XDEW", 1000)]
    flags = check_fees(hs, reg2, 0.0015, frozenset({"VUAG"}))
    w = next(f for f in flags if f.severity == "warn")
    assert "0.22%" in w.message and "VUAG" in w.message
    flags = check_fees(hs, reg2, 0.0015, frozenset({"SOMETHING"}))
    w = next(f for f in flags if f.severity == "warn")
    assert "VUAG" not in w.message and "no cheaper" in w.message


def test_fee_availability_falls_back_to_registry_flag() -> None:
    reg = registry()
    vuag = replace(reg.get("VUAG"), available_on_t212=True)  # type: ignore[type-var]
    reg2 = type(reg)({**reg.by_symbol, "VUAG": vuag})
    hs = [holding("isa", "XDEW", 1000)]
    w = next(f for f in check_fees(hs, reg2, 0.0015, None) if f.severity == "warn")
    assert "VUAG" in w.message
    # unknown flag and no instrument list: not suggested
    w = next(f for f in check_fees(hs, registry(), 0.0015, None) if f.severity == "warn")
    assert "VUAG" not in w.message


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
