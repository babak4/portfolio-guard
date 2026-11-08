from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from guard import paths
from guard.secrets import SecretNotFound, get_secret


def test_get_secret_returns_fake_value_and_never_logs_it(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GUARD_FAKE_SECRETS", json.dumps({"guard_t212_key_isa": "s3cr3t-value"}))
    with caplog.at_level(logging.DEBUG):
        assert get_secret("guard_t212_key_isa") == "s3cr3t-value"
    out = capsys.readouterr()
    assert "s3cr3t-value" not in caplog.text + out.out + out.err


def test_missing_secret_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUARD_FAKE_SECRETS", "{}")
    with pytest.raises(SecretNotFound):
        get_secret("nope")


def test_data_dir_inside_repo_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUARD_DATA_DIR", str(paths.REPO_ROOT / "data"))
    with pytest.raises(paths.DataDirError):
        paths.data_dir()


def test_data_dir_unset_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GUARD_DATA_DIR", raising=False)
    with pytest.raises(paths.DataDirError):
        paths.data_dir()


def test_data_dir_outside_repo_is_created(data_dir: Path) -> None:
    assert paths.data_dir() == data_dir.resolve()
    assert data_dir.is_dir()
