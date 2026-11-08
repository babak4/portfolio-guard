"""Data-directory resolution. The data directory must live outside the repository."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class DataDirError(RuntimeError):
    pass


def data_dir() -> Path:
    raw = os.environ.get("GUARD_DATA_DIR")
    if not raw:
        raise DataDirError("GUARD_DATA_DIR is not set")
    p = Path(raw).expanduser().resolve()
    if p == REPO_ROOT or REPO_ROOT in p.parents:
        raise DataDirError(f"GUARD_DATA_DIR must be outside the repository: {p}")
    p.mkdir(parents=True, exist_ok=True)
    return p


def subdir(*parts: str) -> Path:
    p = data_dir().joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p
