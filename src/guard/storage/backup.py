"""Backups of the data directory: snapshot, verify, restore and prune.

A backup is a folder ``backup-<UTC timestamp>/`` holding a consistent copy of ``state/ops.sqlite``
(taken with the sqlite3 backup API), the DuckDB warehouse (copied while the writer lock is held),
the ``config/`` folder and ``manual_holdings.yaml``, plus ``manifest.json`` with the sha256 of every
file. ``.env`` files, sops secrets files and the licensed MSCI input are never copied; they are
re-created from the owner's password manager and the data vendor.

Command line: ``python -m guard.storage.backup backup|restore|prune|verify|latest``.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from collections.abc import Callable, Hashable, Iterable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from guard.storage import run_store
from guard.storage.lock import writer_lock
from guard.storage.ops import open_ops

__all__ = ["BackupError", "backup", "folder_name", "latest", "main", "prune", "restore", "verify"]

MANIFEST = "manifest.json"
LAST_BACKUP_KEY = "last_backup_at"
OPS_REL = "state/ops.sqlite"
WAREHOUSE_REL = "warehouse/guard.duckdb"
_NAME_RE = re.compile(r"^backup-(\d{8}T\d{6})Z$")
_NEVER_COPY = (".env", ".env.*", "*.enc.yaml", "msci*")
_DEFAULT_LOCK_WAIT = 60.0


class BackupError(RuntimeError):
    pass


def folder_name(when: datetime) -> str:
    return "backup-" + when.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _parse_name(name: str) -> datetime | None:
    m = _NAME_RE.match(name)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=UTC)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_secret_name(name: str) -> bool:
    return any(fnmatch.fnmatch(name.lower(), pat) for pat in _NEVER_COPY)


@contextmanager
def _locked(
    data_dir: Path, wait_seconds: float, names: Sequence[str] = ("run", "writer")
) -> Iterator[None]:
    """Hold the named locks of ``data_dir`` (the lock module resolves it from the environment)."""
    previous = os.environ.get("GUARD_DATA_DIR")
    os.environ["GUARD_DATA_DIR"] = str(data_dir)
    try:
        with ExitStack() as stack:
            for name in names:
                acquired = stack.enter_context(writer_lock(name, wait_seconds=wait_seconds))
                if not acquired:
                    raise BackupError(
                        f"the {name} lock is held by another process; try again later"
                    )
            yield
    finally:
        if previous is None:
            os.environ.pop("GUARD_DATA_DIR", None)
        else:
            os.environ["GUARD_DATA_DIR"] = previous


def _make_private(root: Path) -> None:
    """Owner-only permissions: directories 0700, files 0600."""
    root.chmod(0o700)
    for path in root.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)


def _snapshot_sqlite(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(src)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
        target.execute("PRAGMA journal_mode=DELETE")
    finally:
        target.close()
        source.close()


def _copy_warehouse(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:  # fold the write-ahead log into the file; a copy without it could be stale
        conn = duckdb.connect(str(src))
        try:
            conn.execute("CHECKPOINT")
        finally:
            conn.close()
    except duckdb.Error as e:
        raise BackupError(f"warehouse checkpoint failed: {e}") from e
    shutil.copy2(src, dst)
    wal = src.with_name(src.name + ".wal")
    if wal.exists():
        shutil.copy2(wal, dst.with_name(dst.name + ".wal"))


def _copy_config(src: Path, dst: Path) -> None:
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if not path.is_file() or any(_is_secret_name(part) for part in rel.parts):
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def backup(
    data_dir: Path,
    dest: Path,
    now: datetime,
    *,
    lock_wait_seconds: float = _DEFAULT_LOCK_WAIT,
) -> Path:
    """Write a verified snapshot of ``data_dir`` into ``dest/backup-<timestamp>/``."""
    data_dir = data_dir.resolve()
    final = dest / folder_name(now)
    if final.exists():
        raise BackupError(f"backup folder exists: {final}")
    with _locked(data_dir, lock_wait_seconds):
        dest.mkdir(mode=0o700, parents=True, exist_ok=True)
        staging = dest / (final.name + ".partial")
        if staging.exists():
            shutil.rmtree(staging)
        try:
            if (data_dir / OPS_REL).exists():
                _snapshot_sqlite(data_dir / OPS_REL, staging / OPS_REL)
            if (data_dir / WAREHOUSE_REL).exists():
                _copy_warehouse(data_dir / WAREHOUSE_REL, staging / WAREHOUSE_REL)
            if (data_dir / "config").is_dir():
                _copy_config(data_dir / "config", staging / "config")
            manual = data_dir / "manual_holdings.yaml"
            if manual.is_file():
                shutil.copy2(manual, staging / manual.name)
            if not staging.exists():
                raise BackupError(f"nothing to back up in {data_dir}")
            problems = check_data(staging)
            if problems:
                raise BackupError("the snapshot failed its integrity check: " + "; ".join(problems))
            files = {
                str(p.relative_to(staging)): _sha256(p)
                for p in sorted(staging.rglob("*"))
                if p.is_file()
            }
            manifest = {"created_at": now.astimezone(UTC).isoformat(), "files": files}
            (staging / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True))
            _make_private(staging)
            staging.rename(final)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        if (data_dir / OPS_REL).exists():
            _record_last_backup(now)
    return final


def _record_last_backup(now: datetime) -> None:
    """Note the time of this backup in the control table (the data dir is already resolved)."""
    try:
        conn = open_ops()
        try:
            run_store.set_control(conn, LAST_BACKUP_KEY, now.astimezone(UTC).isoformat(), now)
        finally:
            conn.close()
    except sqlite3.Error as e:
        raise BackupError(f"backup written but last_backup_at could not be recorded: {e}") from e


def _load_manifest(backup_dir: Path) -> dict[str, str]:
    try:
        raw = json.loads((backup_dir / MANIFEST).read_text("utf-8"))
        files = raw["files"]
    except (OSError, ValueError, KeyError) as e:
        raise BackupError(f"manifest unreadable in {backup_dir}: {e}") from e
    if not isinstance(files, dict):
        raise BackupError(f"manifest malformed in {backup_dir}")
    return {str(k): str(v) for k, v in files.items()}


def verify(backup_dir: Path) -> list[str]:
    """Return a list of problems (empty when every file matches the manifest)."""
    try:
        files = _load_manifest(backup_dir)
    except BackupError as e:
        return [str(e)]
    problems: list[str] = []
    root = backup_dir.resolve()
    for rel, digest in files.items():
        path = (backup_dir / rel).resolve()
        if not path.is_relative_to(root):
            problems.append(f"unsafe path in manifest: {rel}")
        elif not path.is_file():
            problems.append(f"missing: {rel}")
        elif _sha256(path) != digest:
            problems.append(f"checksum mismatch: {rel}")
    on_disk = {str(p.relative_to(backup_dir)) for p in backup_dir.rglob("*") if p.is_file()} - {
        MANIFEST
    }
    problems.extend(f"unexpected file: {extra}" for extra in sorted(on_disk - set(files)))
    return problems


def _stage(backup_dir: Path, files: Iterable[str], staged: Path) -> None:
    staged.mkdir(mode=0o700, parents=True)
    for rel in files:
        target = staged / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_dir / rel, target)
    _make_private(staged)


def restore(
    backup_dir: Path,
    data_dir: Path,
    *,
    force: bool = False,
    lock_wait_seconds: float = 5.0,
    now: datetime | None = None,
) -> None:
    """Verify ``backup_dir``, stage and check a copy, then swap it in for ``data_dir``.

    A non-empty ``data_dir`` needs ``force``; the run, writer and migrate locks must all be free.
    The old directory is kept as ``<data_dir>.pre-restore-<stamp>`` (inside ``data_dir`` when it is
    a mount point and cannot be renamed); files that are not in the backup do not survive.
    """
    problems = verify(backup_dir)
    if problems:
        raise BackupError("backup failed verification: " + "; ".join(problems))
    data_dir = data_dir.resolve() if data_dir.exists() else data_dir.absolute()
    occupied = data_dir.exists() and any(data_dir.iterdir())
    if occupied and not force:
        raise BackupError(f"data dir is not empty: {data_dir} (use force to overwrite)")
    stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    in_place = occupied and os.path.ismount(data_dir)
    holder = data_dir if in_place else data_dir.parent
    staged = holder / (
        f".restore-staging-{stamp}" if in_place else f"{data_dir.name}.restore-{stamp}"
    )
    files = list(_load_manifest(backup_dir))
    with (
        _locked(data_dir, lock_wait_seconds, ("run", "writer", "migrate"))
        if occupied
        else _nolock()
    ):
        if staged.exists():
            shutil.rmtree(staged)
        try:
            _stage(backup_dir, files, staged)
            found = check_data(staged)
            if found:
                raise BackupError("restored data failed its integrity check: " + "; ".join(found))
            _swap(data_dir, staged, stamp, occupied=occupied, in_place=in_place)
        except BaseException:
            shutil.rmtree(staged, ignore_errors=True)
            raise


@contextmanager
def _nolock() -> Iterator[None]:
    yield


def _swap(data_dir: Path, staged: Path, stamp: str, *, occupied: bool, in_place: bool) -> None:
    if in_place:
        aside = data_dir / f".pre-restore-{stamp}"
        aside.mkdir(mode=0o700)
        old = [p for p in data_dir.iterdir() if p not in (staged, aside)]
        for path in old:
            path.rename(aside / path.name)
        try:
            for path in list(staged.iterdir()):
                path.rename(data_dir / path.name)
        except BaseException:
            for path in old:
                (aside / path.name).rename(path)
            raise
        staged.rmdir()
        return
    if occupied:
        aside = data_dir.with_name(f"{data_dir.name}.pre-restore-{stamp}")
        if aside.exists():
            raise BackupError(f"pre-restore folder already exists: {aside}")
        data_dir.rename(aside)
        try:
            staged.rename(data_dir)
        except BaseException:
            aside.rename(data_dir)
            raise
        return
    if data_dir.exists():
        data_dir.rmdir()
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    staged.rename(data_dir)


def check_data(data_dir: Path) -> list[str]:
    """Integrity checks on a (restored) data directory. Returns problems."""
    problems: list[str] = []
    ops = data_dir / OPS_REL
    if not ops.exists():
        problems.append(f"missing: {OPS_REL}")
    else:
        conn = sqlite3.connect(f"file:{ops}?mode=ro", uri=True)
        try:
            if conn.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                problems.append("ops.sqlite integrity_check failed")
        except sqlite3.Error as e:
            problems.append(f"ops.sqlite unreadable: {e}")
        finally:
            conn.close()
    wh = data_dir / WAREHOUSE_REL
    if wh.exists():
        try:
            duckdb.connect(str(wh), read_only=True).close()
        except duckdb.Error as e:
            problems.append(f"warehouse unreadable: {e}")
    return problems


def latest(dest: Path) -> Path | None:
    names = sorted(
        (p for p in dest.iterdir() if p.is_dir() and _parse_name(p.name)) if dest.is_dir() else [],
        key=lambda p: p.name,
    )
    return names[-1] if names else None


def prune(
    dest: Path, keep_daily: int = 14, keep_weekly: int = 8, keep_monthly: int = 24
) -> list[str]:
    """Delete verified backups outside the newest-per-day, -week and -month sets; return names.

    Only backups that pass ``verify`` take a retention slot. The newest verified one is never
    deleted, and a backup that does not verify is neither counted nor deleted (see ``unverified``).
    """
    if not dest.is_dir():
        return []
    found = sorted(
        (
            (when, p)
            for p in dest.iterdir()
            if p.is_dir() and (when := _parse_name(p.name)) and not verify(p)
        ),
        key=lambda t: t[0],
        reverse=True,
    )

    def newest_per(bucket: Callable[[datetime], Hashable], limit: int) -> set[str]:
        seen: dict[Hashable, str] = {}
        for when, path in found:  # newest first, so the first seen per bucket is its newest
            seen.setdefault(bucket(when), path.name)
        ordered = list(seen.values())  # insertion order is newest bucket first
        return set(ordered[: max(0, limit)])

    def day(w: datetime) -> date:
        return w.date()

    def week(w: datetime) -> tuple[int, int]:
        iso = w.isocalendar()
        return (iso.year, iso.week)

    def month(w: datetime) -> tuple[int, int]:
        return (w.year, w.month)

    keep = (
        newest_per(day, keep_daily)
        | newest_per(week, keep_weekly)
        | newest_per(month, keep_monthly)
    )
    if found:
        keep.add(found[0][1].name)
    removed = []
    for _, path in found:
        if path.name not in keep:
            shutil.rmtree(path)
            removed.append(path.name)
    return removed


def unverified(dest: Path) -> list[str]:
    """Names of backup folders in ``dest`` that fail ``verify`` (left alone by ``prune``)."""
    if not dest.is_dir():
        return []
    return sorted(
        p.name for p in dest.iterdir() if p.is_dir() and _parse_name(p.name) and verify(p)
    )


def _dest(arg: str | None) -> Path:
    raw = arg or os.environ.get("GUARD_BACKUP_DIR")
    if not raw:
        raise BackupError("give --dest or set GUARD_BACKUP_DIR")
    return Path(raw).expanduser()


def _data_dir(arg: str | None) -> Path:
    raw = arg or os.environ.get("GUARD_DATA_DIR")
    if not raw:
        raise BackupError("give --data-dir or set GUARD_DATA_DIR")
    return Path(raw).expanduser().resolve()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m guard.storage.backup")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("backup", help="snapshot the data directory")
    b.add_argument("--dest")
    b.add_argument("--data-dir")
    r = sub.add_parser("restore", help="restore a backup folder into a data directory")
    r.add_argument("source")
    r.add_argument("--data-dir")
    r.add_argument("--force", action="store_true")
    p = sub.add_parser("prune", help="thin out old backups")
    p.add_argument("--dest")
    p.add_argument("--keep-daily", type=int, default=14)
    p.add_argument("--keep-weekly", type=int, default=8)
    p.add_argument("--keep-monthly", type=int, default=24)
    v = sub.add_parser("verify", help="check a backup against its manifest")
    v.add_argument("source")
    v.add_argument("--data-dir", help="also run integrity checks on this restored data directory")
    lt = sub.add_parser("latest", help="print the newest backup folder")
    lt.add_argument("dest", nargs="?")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "backup":
            print(backup(_data_dir(args.data_dir), _dest(args.dest), datetime.now(UTC)))
        elif args.command == "restore":
            restore(Path(args.source), _data_dir(args.data_dir), force=args.force)
            print(f"restored into {_data_dir(args.data_dir)}")
        elif args.command == "prune":
            removed = prune(_dest(args.dest), args.keep_daily, args.keep_weekly, args.keep_monthly)
            print(f"removed {len(removed)} old backups")
            for name in unverified(_dest(args.dest)):
                print(f"warning: {name} does not verify and was left in place", file=sys.stderr)
        elif args.command == "verify":
            problems = verify(Path(args.source))
            if not problems and args.data_dir:
                problems = check_data(Path(args.data_dir))
            for problem in problems:
                print(problem, file=sys.stderr)
            if problems:
                return 1
            print("ok")
        else:
            found = latest(_dest(args.dest))
            if found is None:
                print("no backups found", file=sys.stderr)
                return 1
            print(found)
    except BackupError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
