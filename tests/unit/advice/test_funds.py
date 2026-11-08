from __future__ import annotations

from pathlib import Path

import pytest

from guard.advice.fund_registry import FundFileError, load_registry, packaged_registry
from guard.advice.funds import symbol_of


@pytest.mark.parametrize(
    ("ticker", "symbol"),
    [
        ("VWRPl_EQ", "VWRP"),
        ("CSH2l_EQ", "CSH2"),
        ("VWCEd_EQ", "VWCE"),
        ("BRK_B_US_EQ", "BRK.B"),
        ("BRKB_US_EQ", "BRKB"),
        ("AAPL_US_EQ", "AAPL"),
    ],
)
def test_symbol_of(ticker: str, symbol: str) -> None:
    assert symbol_of(ticker) == symbol


def test_registry_finds_by_ticker_including_brkb_forms() -> None:
    reg = packaged_registry()
    assert reg.find("VWRPl_EQ") is not None
    assert reg.find("VWRPl_EQ").symbol == "VWRP"  # type: ignore[union-attr]
    for ticker in ("BRK_B_US_EQ", "BRKB_US_EQ"):
        found = reg.find(ticker)
        assert found is not None and found.symbol == "BRK.B"
    assert reg.find("NOPEl_EQ") is None


def test_seed_values() -> None:
    reg = packaged_registry()
    vwrp = reg.get("VWRP")
    assert vwrp is not None and vwrp.ongoing_charge == 0.0019 and not vwrp.us_only
    assert not vwrp.verified and "factsheet" in vwrp.note
    assert reg.get("VUAG").us_only  # type: ignore[union-attr]
    assert reg.get("BRK.B").us_only  # type: ignore[union-attr]
    assert reg.get("VHVG").ongoing_charge is None  # type: ignore[union-attr]
    assert reg.get("CSH2").role == "safe"  # type: ignore[union-attr]
    assert reg.get("CUKX").watchlist  # type: ignore[union-attr]


def test_override_merges_per_symbol(tmp_path: Path) -> None:
    f = tmp_path / "funds.yaml"
    f.write_text(
        "funds:\n"
        "  - symbol: VWRP\n    ongoing_charge: 0.0022\n    available_on_t212: true\n"
        "  - symbol: NEWF\n    name: New fund\n    role: growth\n    category: global\n"
    )
    reg = load_registry(override_path=f)
    vwrp = reg.get("VWRP")
    assert vwrp is not None
    assert vwrp.ongoing_charge == 0.0022 and vwrp.available_on_t212 is True
    assert vwrp.name == "Vanguard FTSE All-World Acc"
    assert reg.get("NEWF") is not None


def test_override_rejects_unknown_key_and_bad_new_fund(tmp_path: Path) -> None:
    f = tmp_path / "funds.yaml"
    f.write_text("funds:\n  - symbol: VWRP\n    ocf: 0.1\n")
    with pytest.raises(FundFileError):
        load_registry(override_path=f)
    f.write_text("funds:\n  - symbol: NEWF\n")
    with pytest.raises(FundFileError):
        load_registry(override_path=f)


def test_data_dir_override_is_picked_up(data_dir: Path) -> None:
    (data_dir / "config").mkdir(parents=True)
    (data_dir / "config" / "funds.yaml").write_text(
        "funds:\n  - symbol: VWRP\n    ongoing_charge: 0.005\n"
    )
    assert load_registry().get("VWRP").ongoing_charge == 0.005  # type: ignore[union-attr]


def test_alternatives_same_category_and_cheaper() -> None:
    reg = packaged_registry()
    alts = [f.symbol for f in reg.alternatives(reg.get("VWRP"))]  # type: ignore[arg-type]
    assert alts == []  # VWCE has the same charge; VHVG/ACWI unknown
    alts = [f.symbol for f in reg.alternatives(reg.get("XDEW"))]  # type: ignore[arg-type]
    assert alts == ["VUAG"]
