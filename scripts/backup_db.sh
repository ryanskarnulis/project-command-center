#!/usr/bin/env bash
#
# Nightly SQLite backup for Project Command Center (Sprint 6).
#
# Uses Python's stdlib `sqlite3.Connection.backup()`, which takes a consistent
# snapshot of a live database (a proper online backup — NOT a plain file copy,
# which can capture a torn write while the app is mid-transaction). Safe to run
# while the backend is up. No external `sqlite3` CLI needed — the project already
# requires Python 3.11+, and the stdlib does exactly what the CLI's `.backup` does.
#
# Snapshots are written atomically: the copy lands on a temporary name inside
# data/backups/ and is renamed into place only after it completes, so a file
# matching `app-*.db` is always a whole snapshot (see #263). Rename is atomic
# on the same filesystem, which is why the temporary lives in the backup
# directory rather than /tmp.
#
# Cron example (daily at 02:00) — edit the path to match your checkout:
#   0 2 * * * /home/you/project-command-center/scripts/backup_db.sh

set -euo pipefail

# Resolve the repo root from this script's own location, so cron's CWD is irrelevant.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DB_PATH="${REPO_ROOT}/data/app.db"
BACKUP_DIR="${REPO_ROOT}/data/backups"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

# shellcheck source=scripts/backup_retention.sh
source "${SCRIPT_DIR}/backup_retention.sh"

# Validate up front: a bad retention value must never reach the prune step,
# and must not leave behind a half-done run either.
validate_retention_days "${RETENTION_DAYS}" || exit 1

PYTHON="${PYTHON:-python3}"
if ! command -v "${PYTHON}" >/dev/null 2>&1; then
  echo "backup_db.sh: ${PYTHON} not found on PATH" >&2
  exit 1
fi

if [[ ! -f "${DB_PATH}" ]]; then
  echo "backup_db.sh: database not found at ${DB_PATH}" >&2
  exit 1
fi

mkdir -p "${BACKUP_DIR}"
DEST="${BACKUP_DIR}/app-$(date +%Y%m%d-%H%M%S).db"

# Write to a temporary name first. The `.tmp.<pid>` suffix keeps the partial out
# of the `app-*.db` glob that prune_backups and any restore ("newest snapshot")
# match — an unfinished copy must never look like a snapshot — and `$$` keeps
# two runs that start in the same second off each other's file. Such a pair now
# resolves to one complete snapshot (the later rename wins) instead of two
# writers interleaving into a single half-valid file.
TMP_DEST="${DEST}.tmp.$$"

# Anything that ends the run before the rename takes the partial with it:
# the snapshot itself plus the journal/WAL sidecars SQLite may leave beside it.
cleanup_tmp_dest() {
  rm -f "${TMP_DEST}" "${TMP_DEST}-journal" "${TMP_DEST}-wal" "${TMP_DEST}-shm"
}
trap cleanup_tmp_dest EXIT

# Consistent online snapshot via the stdlib backup API.
"${PYTHON}" - "${DB_PATH}" "${TMP_DEST}" <<'PY'
import sqlite3
import sys

src, dest = sys.argv[1], sys.argv[2]
source = sqlite3.connect(src)
backup = sqlite3.connect(dest)
try:
    with backup:
        source.backup(backup)
finally:
    backup.close()
    source.close()
PY

# Publish the finished snapshot under its real name. Rename is atomic within
# the filesystem, so readers see either no file or the complete one.
mv -- "${TMP_DEST}" "${DEST}"
echo "backup_db.sh: wrote ${DEST}"

# Prune backups older than the retention window, plus any temporaries a
# hard kill (SIGKILL, power loss) stranded before the trap could run.
prune_backups "${BACKUP_DIR}" "${RETENTION_DAYS}"
prune_stale_temp_snapshots "${BACKUP_DIR}" "${RETENTION_DAYS}"
