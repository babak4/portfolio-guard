from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from guard.storage import backup as bk
from guard.storage import open_ops, open_warehouse, writer_lock

NOW = datetime(2026, 11, 8, 3, 0, tzinfo=UTC)


def _seed(data: Path) -> None:
    conn = open_ops()
    conn.execute(
        "INSERT INTO run (run_id, kind, slot, status, started_at) VALUES ('r1','weekly','s1','ok','t')"
    )
    conn.close()
    wh = open_warehouse()
    wh.execute("CREATE TABLE IF NOT EXISTS probe (k INTEGER, v VARCHAR)")
    wh.execute("INSERT INTO probe VALUES (1, 'a'), (2, 'b')")
    wh.close()
    (data / "config").mkdir(exist_ok=True)
    (data / "config" / "overrides.yaml").write_text("x: 1\n")
    (data / "config" / "funds.yaml").write_text("funds: []\n")
    (data / ".env").write_text("GUARD_T212_KEY_GIA=topsecret\n")
    (data / "secrets.enc.yaml").write_text("sops: topsecret\n")
    (data / "inputs").mkdir()
    (data / "inputs" / "msci_acwi_nr.csv").write_text("licensed\n")


def _files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def test_round_trip_restores_ops_rows_and_warehouse(data_dir: Path, tmp_path: Path) -> None:
    _seed(data_dir)
    out = bk.backup(data_dir, tmp_path / "bk", NOW)
    target = tmp_path / "restored"
    bk.restore(out, target)

    ops = sqlite3.connect(target / "state" / "ops.sqlite")
    assert ops.execute("SELECT run_id, status FROM run").fetchall() == [("r1", "ok")]
    assert ops.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    ops.close()
    import duckdb

    wh = duckdb.connect(str(target / "warehouse" / "guard.duckdb"), read_only=True)
    assert wh.execute("SELECT k, v FROM probe ORDER BY k").fetchall() == [(1, "a"), (2, "b")]
    wh.close()
    assert (target / "config" / "overrides.yaml").read_text() == "x: 1\n"
    assert (target / "config" / "funds.yaml").exists()


def test_secrets_env_and_msci_never_copied(data_dir: Path, tmp_path: Path) -> None:
    _seed(data_dir)
    # also try to smuggle them into config/
    (data_dir / "config" / ".env").write_text("K=topsecret\n")
    (data_dir / "config" / "extra.enc.yaml").write_text("topsecret\n")
    out = bk.backup(data_dir, tmp_path / "bk", NOW)
    names = _files(out)
    assert not any(".env" in n or "enc.yaml" in n or "msci" in n for n in names)
    for p in out.rglob("*"):
        if p.is_file():
            assert b"topsecret" not in p.read_bytes()


def test_manifest_lists_sha256_of_each_file(data_dir: Path, tmp_path: Path) -> None:
    _seed(data_dir)
    out = bk.backup(data_dir, tmp_path / "bk", NOW)
    manifest = json.loads((out / "manifest.json").read_text())
    assert set(manifest["files"]) == set(_files(out)) - {"manifest.json"}
    assert all(len(h) == 64 for h in manifest["files"].values())
    assert bk.verify(out) == []


def test_corrupted_file_detected_and_restore_refuses(data_dir: Path, tmp_path: Path) -> None:
    _seed(data_dir)
    out = bk.backup(data_dir, tmp_path / "bk", NOW)
    with (out / "config" / "overrides.yaml").open("ab") as fh:
        fh.write(b"tamper")
    problems = bk.verify(out)
    assert any("overrides.yaml" in p for p in problems)
    target = tmp_path / "restored"
    with pytest.raises(bk.BackupError):
        bk.restore(out, target)
    assert not target.exists() or not any(target.iterdir())


def test_missing_and_extra_files_detected(data_dir: Path, tmp_path: Path) -> None:
    _seed(data_dir)
    out = bk.backup(data_dir, tmp_path / "bk", NOW)
    (out / "config" / "funds.yaml").unlink()
    (out / "stray.txt").write_text("x")
    problems = " ".join(bk.verify(out))
    assert "funds.yaml" in problems and "stray.txt" in problems


def test_restore_refuses_non_empty_dir_without_force(data_dir: Path, tmp_path: Path) -> None:
    _seed(data_dir)
    out = bk.backup(data_dir, tmp_path / "bk", NOW)
    target = tmp_path / "restored"
    target.mkdir()
    (target / "keep.txt").write_text("mine")
    with pytest.raises(bk.BackupError, match="not empty"):
        bk.restore(out, target)
    assert (target / "keep.txt").read_text() == "mine"
    bk.restore(out, target, force=True)
    assert (target / "state" / "ops.sqlite").exists()


def test_backup_respects_writer_lock(data_dir: Path, tmp_path: Path) -> None:
    _seed(data_dir)
    held = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with writer_lock("writer"):
            held.set()
            release.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(5)
    try:
        with pytest.raises(bk.BackupError, match="writer lock"):
            bk.backup(data_dir, tmp_path / "bk", NOW, lock_wait_seconds=0.2)
    finally:
        release.set()
        t.join()
    assert not (tmp_path / "bk").exists() or not any((tmp_path / "bk").iterdir())


def test_backup_is_consistent_while_ops_is_open_in_wal(data_dir: Path, tmp_path: Path) -> None:
    _seed(data_dir)
    live = open_ops()  # stays open with WAL
    live.execute(
        "INSERT INTO run (run_id, kind, slot, status, started_at) VALUES ('r2','daily','s2','ok','t')"
    )
    out = bk.backup(data_dir, tmp_path / "bk", NOW)
    live.close()
    snap = sqlite3.connect(out / "state" / "ops.sqlite")
    assert snap.execute("SELECT count(*) FROM run").fetchone() == (2,)
    snap.close()


def test_backup_refuses_existing_destination_folder(data_dir: Path, tmp_path: Path) -> None:
    _seed(data_dir)
    bk.backup(data_dir, tmp_path / "bk", NOW)
    with pytest.raises(bk.BackupError, match="exists"):
        bk.backup(data_dir, tmp_path / "bk", NOW)


def test_latest_returns_newest(data_dir: Path, tmp_path: Path) -> None:
    _seed(data_dir)
    bk.backup(data_dir, tmp_path / "bk", NOW)
    newer = bk.backup(data_dir, tmp_path / "bk", NOW + timedelta(hours=5))
    assert bk.latest(tmp_path / "bk") == newer


def _make(dest: Path, when: datetime) -> str:
    name = bk.folder_name(when)
    (dest / name).mkdir(parents=True)
    return name


def test_prune_keeps_daily_weekly_monthly_sets(tmp_path: Path) -> None:
    dest = tmp_path / "bk"
    start = datetime(2024, 1, 1, 3, 0, tzinfo=UTC)
    names = [_make(dest, start + timedelta(days=i)) for i in range(0, 900)]
    (dest / "notes.txt").write_text("not a backup")
    (dest / "foreign-dir").mkdir()
    removed = bk.prune(dest, keep_daily=14, keep_weekly=8, keep_monthly=24)
    kept = {p.name for p in dest.iterdir() if p.is_dir()}
    assert (dest / "notes.txt").exists() and "foreign-dir" in kept
    kept &= set(names)
    assert set(names) - kept == set(removed)
    newest = names[-1]
    # the 14 most recent days are all kept
    assert set(names[-14:]) <= kept
    # one per week for the last 8 weeks beyond that: at most 14 + 8 + 24 survive
    assert len(kept) <= 14 + 8 + 24
    assert len(kept) >= 24 + 14  # months and days do not fully overlap
    assert newest in kept
    # old data is thinned to roughly one per month
    old = [n for n in kept if n < names[-200]]
    assert 0 < len(old) <= 24
    assert names[0] not in kept


def test_prune_small_set_keeps_everything(tmp_path: Path) -> None:
    dest = tmp_path / "bk"
    for i in range(5):
        _make(dest, NOW - timedelta(days=i))
    assert bk.prune(dest) == []


def test_prune_missing_dest_is_noop(tmp_path: Path) -> None:
    assert bk.prune(tmp_path / "nope") == []


def test_cli_backup_verify_restore(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(data_dir)
    dest = tmp_path / "bk"
    assert bk.main(["backup", "--dest", str(dest)]) == 0
    out = capsys.readouterr().out.strip()
    assert bk.main(["verify", out]) == 0
    target = tmp_path / "r"
    assert bk.main(["restore", out, "--data-dir", str(target)]) == 0
    assert bk.main(["verify", out, "--data-dir", str(target)]) == 0
    assert bk.main(["latest", str(dest)]) == 0
    assert bk.main(["prune", "--dest", str(dest)]) == 0


def test_cli_verify_fails_on_corruption(data_dir: Path, tmp_path: Path) -> None:
    _seed(data_dir)
    out = bk.backup(data_dir, tmp_path / "bk", NOW)
    (out / "config" / "funds.yaml").write_text("changed")
    assert bk.main(["verify", str(out)]) == 1
