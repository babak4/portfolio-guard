"""Numbered SQL migrations, applied once each and recorded by file name."""

from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable


def migration_files(kind: str) -> list[Traversable]:
    root = resources.files("guard.storage").joinpath("migrations", kind)
    return sorted((p for p in root.iterdir() if p.name.endswith(".sql")), key=lambda p: p.name)
