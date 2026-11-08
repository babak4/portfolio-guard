from __future__ import annotations

from pathlib import Path

import pytest

from guard.config import ConfigError, load_config, read_version


def test_missing_override_key_resolves_to_prd_default(data_dir: Path) -> None:
    (data_dir / "config").mkdir(parents=True)
    (data_dir / "config" / "overrides.yaml").write_text("ladder:\n  cooldown_weeks: 3\n")
    cfg, _ = load_config()
    assert cfg.ladder.cooldown_weeks == 3
    assert cfg.ladder.equity_floor == 0.50
    assert cfg.ladder.min_shift == 0.05
    assert cfg.ladder.reentry_tranches == 4
    assert cfg.schedule.weekly_weekday == "wednesday"
    assert cfg.schedule.timezone == "Europe/London"
    assert cfg.daily_check.day_drop == 0.05
    assert cfg.portfolio.gia_monthly_contribution == 1000
    assert [s.equity for s in cfg.ladder.steps] == [0.90, 0.75, 0.60, 0.50]


def test_unknown_override_key_raises(data_dir: Path) -> None:
    (data_dir / "config").mkdir(parents=True)
    (data_dir / "config" / "overrides.yaml").write_text("ladder:\n  cooldown_week: 3\n")
    with pytest.raises(ConfigError):
        load_config()


def test_changed_config_gets_new_version_and_old_stays_readable(data_dir: Path) -> None:
    cfg1, v1 = load_config()
    (data_dir / "config").mkdir(parents=True, exist_ok=True)
    (data_dir / "config" / "overrides.yaml").write_text("ladder:\n  max_weekly_change: 0.2\n")
    cfg2, v2 = load_config()
    assert v1 != v2
    assert read_version(v1) == cfg1
    assert read_version(v2) == cfg2
    assert read_version(v1).ladder.max_weekly_change == 0.15


def test_same_config_same_version(data_dir: Path) -> None:
    assert load_config()[1] == load_config()[1]


def test_unknown_version_raises(data_dir: Path) -> None:
    with pytest.raises(ConfigError):
        read_version("deadbeefdeadbeef")


def test_allocation_defaults_to_no_fund_targets_and_a_small_minimum_trade(data_dir: Path) -> None:
    cfg, _ = load_config()
    assert cfg.allocation.growth_targets == {}
    assert cfg.allocation.min_trade_gbp == 25
    assert cfg.constraints.us_exposure_cap == 0.50
    assert cfg.constraints.fee_ceiling == 0.0015
    assert cfg.ladder.max_weekly_change == 0.15
