from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from guard.ingest.manual import load_manual_holdings
from guard.ingest.model import IngestIntegrityError

YAML = """
- {account_id: investengine-gia, platform: investengine, isin: GB00B3X7QG63, name: Vanguard S&P 500, quantity: 10.5, value_gbp: 1234.56}
- {account_id: investengine-gia, platform: investengine, isin: US0846707026, ticker: BRK.B, name: Berkshire, quantity: 1, value_gbp: 400}
"""

CSV = """account_id,platform,isin,ticker,name,quantity,value_gbp
investengine-gia,investengine,GB00B3X7QG63,,Vanguard S&P 500,10.5,1234.56
"""


def test_yaml_rows_load_with_platform(tmp_path: Path) -> None:
    f = tmp_path / "manual.yaml"
    f.write_text(YAML)
    holdings = load_manual_holdings(f, satellites=("BRK.B",))
    assert [h.platform for h in holdings] == ["investengine", "investengine"]
    assert holdings[0].value_gbp == Decimal("1234.56")
    assert [h.excluded for h in holdings] == [False, True]


def test_csv_rows_load_with_platform(tmp_path: Path) -> None:
    f = tmp_path / "manual.csv"
    f.write_text(CSV)
    (h,) = load_manual_holdings(f, satellites=("BRK.B",))
    assert (h.platform, h.account_id, h.isin) == (
        "investengine",
        "investengine-gia",
        "GB00B3X7QG63",
    )
    assert h.quantity == Decimal("10.5")


def test_missing_value_is_rejected(tmp_path: Path) -> None:
    f = tmp_path / "manual.yaml"
    f.write_text("- {account_id: a, platform: investengine, isin: X, name: n, quantity: 1}")
    with pytest.raises(IngestIntegrityError):
        load_manual_holdings(f, satellites=())
