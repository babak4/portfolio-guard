#!/usr/bin/env bash
# Unverified: written from memory, run it once and correct this file.
# Restore the latest backup into a scratch directory, check it, run `status`. Never touches the live
# data directory.
#
#   GUARD_BACKUP_DIR=/path/to/backups ops/restore_drill.sh [scratch-dir]
set -euo pipefail

: "${GUARD_BACKUP_DIR:?set GUARD_BACKUP_DIR to the folder holding backup-* folders}"
RUN="${GUARD_RUN:-uv run python}"

SCRATCH="${1:-$(mktemp -d "${TMPDIR:-/tmp}/guard-restore-drill.XXXXXX")}"
if [ -n "$(ls -A "$SCRATCH" 2>/dev/null)" ]; then
  echo "scratch dir $SCRATCH is not empty" >&2; exit 2
fi
case "$SCRATCH" in
  "${GUARD_DATA_DIR:-/nonexistent}"|"${GUARD_DATA_DIR:-/nonexistent}"/*)
    echo "refusing to restore into the live data dir" >&2; exit 2;;
esac

LATEST="$($RUN -m guard.storage.backup latest "$GUARD_BACKUP_DIR")"
echo "restoring $LATEST into $SCRATCH"
$RUN -m guard.storage.backup verify "$LATEST"
$RUN -m guard.storage.backup restore "$LATEST" --data-dir "$SCRATCH"
$RUN -m guard.storage.backup verify "$LATEST" --data-dir "$SCRATCH"
GUARD_DATA_DIR="$SCRATCH" $RUN -m guard.worker status
echo "DRILL PASSED. Record the date in docs/paper-trading.md. Scratch kept at $SCRATCH (delete when done)."
