from __future__ import annotations

import os
from pathlib import Path

import pytest

from guard import paths
from guard.secrets import SecretNotFound, SecretsFileError, get_secret, parse_env


def write_env(path: Path, text: str, mode: int = 0o600) -> Path:
    path.write_text(text)
    os.chmod(path, mode)
    return path


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for k in ("GUARD_FAKE_SECRETS", "GUARD_ENV_FILE", "GUARD_T212_KEY_GIA", "GUARD_SECRETS_FILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GUARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path / "repo")


def test_parse_env_handles_comments_quotes_and_export() -> None:
    text = (
        "# a comment\n\nGUARD_A=plain\nexport GUARD_B='single quoted'\n"
        'GUARD_C="double # not a comment"\nGUARD_D=value # trailing comment\nGUARD_E=\n'
    )
    assert parse_env(text) == {
        "GUARD_A": "plain",
        "GUARD_B": "single quoted",
        "GUARD_C": "double # not a comment",
        "GUARD_D": "value",
        "GUARD_E": "",
    }


def test_secret_is_read_from_the_env_file_by_its_uppercased_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = write_env(tmp_path / "my.env", "GUARD_T212_KEY_GIA=abc123\n")
    monkeypatch.setenv("GUARD_ENV_FILE", str(f))
    assert get_secret("guard_t212_key_gia") == "abc123"


def test_a_real_environment_variable_beats_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = write_env(tmp_path / "my.env", "GUARD_T212_KEY_GIA=from-file\n")
    monkeypatch.setenv("GUARD_ENV_FILE", str(f))
    monkeypatch.setenv("GUARD_T212_KEY_GIA", "from-env")
    assert get_secret("guard_t212_key_gia") == "from-env"


def test_the_repo_root_env_file_is_found_without_configuration(tmp_path: Path) -> None:
    (tmp_path / "repo").mkdir()
    write_env(tmp_path / "repo" / ".env", "GUARD_T212_KEY_GIA=repo-secret\n")
    assert get_secret("guard_t212_key_gia") == "repo-secret"


def test_a_file_readable_by_others_is_refused_without_echoing_its_contents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = write_env(tmp_path / "my.env", "GUARD_T212_KEY_GIA=very-secret\n", mode=0o644)
    monkeypatch.setenv("GUARD_ENV_FILE", str(f))
    with pytest.raises(SecretsFileError) as e:
        get_secret("guard_t212_key_gia")
    assert "chmod 600" in str(e.value) and "very-secret" not in str(e.value)


def test_an_env_file_named_but_missing_is_an_error_not_a_silent_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GUARD_ENV_FILE", str(tmp_path / "nope.env"))
    with pytest.raises(SecretsFileError):
        get_secret("guard_t212_key_gia")


def test_a_missing_secret_names_where_it_looked_but_never_a_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = write_env(tmp_path / "my.env", "GUARD_OTHER=shh-do-not-print\n")
    monkeypatch.setenv("GUARD_ENV_FILE", str(f))
    with pytest.raises(SecretNotFound) as e:
        get_secret("guard_t212_key_gia")
    assert "GUARD_T212_KEY_GIA" in str(e.value) and "shh-do-not-print" not in str(e.value)


def test_the_example_file_and_gitignore_protect_the_real_one() -> None:
    root = Path(__file__).resolve().parents[2]
    ignore = (root / ".gitignore").read_text().splitlines()
    assert ".env" in ignore and "!.env.example" in ignore
    example = (root / ".env.example").read_text()
    assert "GUARD_T212_KEY_GIA=" in example
    assert all(
        line.split("=", 1)[1].strip() == ""
        for line in example.splitlines()
        if line and not line.startswith("#") and "=" in line
    )
