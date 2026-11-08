"""Secret access. Secrets are never logged or printed.

A secret named ``guard_t212_key_gia`` is looked up, in order, as:

1. ``GUARD_FAKE_SECRETS``: a JSON object, used by tests only.
2. The environment variable with the upper-cased name (``GUARD_T212_KEY_GIA``).
3. A ``.env`` file: ``GUARD_ENV_FILE`` if set, else ``.env`` in the repository root, else
   ``.env`` in the data directory. It must be readable by its owner only (``chmod 600``).
4. A sops-encrypted file (``GUARD_SECRETS_FILE`` or ``<data dir>/secrets.enc.yaml``), when present.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from guard import paths


class SecretNotFound(KeyError):
    pass


class SecretsFileError(RuntimeError):
    """The secrets file is missing or unsafe. The message never contains a secret."""


def parse_env(text: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines: ``#`` comments, optional ``export``, single or double quotes."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, value = line.split("=", 1)
        value = value.strip()
        if value[:1] in ("'", '"') and value.count(value[0]) >= 2:
            quote = value[0]
            value = value[1 : value.index(quote, 1)]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        out[key.strip()] = value
    return out


def _env_file_candidates() -> list[Path]:
    explicit = os.environ.get("GUARD_ENV_FILE")
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise SecretsFileError(f"GUARD_ENV_FILE points at a missing file: {path}")
        return [path]
    found = [paths.REPO_ROOT / ".env"]
    if os.environ.get("GUARD_DATA_DIR"):
        found.append(paths.data_dir() / ".env")
    return [p for p in found if p.is_file()]


def _read_env_file(path: Path) -> dict[str, str]:
    if path.stat().st_mode & 0o077:
        raise SecretsFileError(f"{path} is readable by other users; run: chmod 600 {path}")
    return parse_env(path.read_text("utf-8"))


def _from_sops(name: str) -> str | None:
    from guard.paths import data_dir

    path = Path(os.environ.get("GUARD_SECRETS_FILE", data_dir() / "secrets.enc.yaml"))
    if not path.is_file():
        return None
    out = subprocess.run(
        ["sops", "--decrypt", "--output-type", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    value = json.loads(out).get(name)
    return None if value is None else str(value)


def get_secret(name: str) -> str:
    fake = os.environ.get("GUARD_FAKE_SECRETS")  # JSON object; tests only
    if fake is not None:
        try:
            return str(json.loads(fake)[name])
        except KeyError as e:
            raise SecretNotFound(name) from e

    env_key = name.upper()
    if env_key in os.environ:
        return os.environ[env_key]
    for path in _env_file_candidates():
        values = _read_env_file(path)
        if env_key in values:
            return values[env_key]
    value = _from_sops(name)
    if value is not None:
        return value
    raise SecretNotFound(
        f"{name}: set {env_key} in the environment or in a .env file (see .env.example)"
    )
