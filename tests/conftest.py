from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "guard-data"
    monkeypatch.setenv("GUARD_DATA_DIR", str(d))
    monkeypatch.delenv("GUARD_FAKE_SECRETS", raising=False)
    return d
