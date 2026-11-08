"""Layered configuration: packaged defaults, then optional overrides in the data directory.

Every load is versioned by a hash of its canonical JSON and archived, so a past run can name the
exact configuration it used and the old version stays readable after a change.
"""

from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from guard.config.models import GuardConfig
from guard.paths import subdir


class ConfigError(ValueError):
    pass


def _defaults() -> dict[str, Any]:
    text = resources.files("guard.config").joinpath("defaults/guard.yaml").read_text("utf-8")
    loaded = yaml.safe_load(text)
    assert isinstance(loaded, dict)
    return loaded


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def canonical_json(cfg: GuardConfig) -> str:
    return json.dumps(cfg.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def config_version(cfg: GuardConfig) -> str:
    return hashlib.sha256(canonical_json(cfg).encode()).hexdigest()[:16]


def _versions_dir() -> Path:
    return subdir("config", "versions")


def _overrides_path() -> Path:
    return subdir("config") / "overrides.yaml"


def load_config() -> tuple[GuardConfig, str]:
    """Return the effective configuration and its version hash, archiving the version."""
    merged = _defaults()
    path = _overrides_path()
    if path.exists():
        over = yaml.safe_load(path.read_text("utf-8")) or {}
        if not isinstance(over, dict):
            raise ConfigError("overrides.yaml must be a mapping")
        merged = _merge(merged, over)
    try:
        cfg = GuardConfig.model_validate(merged)
    except ValidationError as e:
        raise ConfigError(str(e)) from e
    version = config_version(cfg)
    archive = _versions_dir() / f"{version}.json"
    if not archive.exists():
        archive.write_text(canonical_json(cfg), "utf-8")
    return cfg, version


def read_version(version: str) -> GuardConfig:
    """Load an archived configuration by its version hash."""
    archive = _versions_dir() / f"{version}.json"
    if not archive.exists():
        raise ConfigError(f"unknown config version: {version}")
    return GuardConfig.model_validate_json(archive.read_text("utf-8"))
