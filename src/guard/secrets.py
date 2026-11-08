"""Secret access. The real backend decrypts a sops file; tests use an env-var fake.

Secrets are never logged or printed.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


class SecretNotFound(KeyError):
    pass


def get_secret(name: str) -> str:
    fake = os.environ.get("GUARD_FAKE_SECRETS")  # JSON object; tests only
    if fake is not None:
        try:
            return str(json.loads(fake)[name])
        except KeyError as e:
            raise SecretNotFound(name) from e
    from guard.paths import data_dir

    path = Path(os.environ.get("GUARD_SECRETS_FILE", data_dir() / "secrets.enc.yaml"))
    out = subprocess.run(
        ["sops", "--decrypt", "--output-type", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    try:
        return str(json.loads(out)[name])
    except KeyError as e:
        raise SecretNotFound(name) from e
