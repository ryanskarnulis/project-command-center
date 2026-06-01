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
# Cron example (daily at 02:00) — edit the path to match your checkout:
#   0 2 * * * /home/you/project-command-center/scripts/backup_db.sh

set -euo pipefail

# Resolve the repo root from this script's own location, so cron's CWD is irrelevant.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DB_PATH="${REPO_ROOT}/data/app.db"
BACKUP_DIR="${REPO_ROOT}/data/backups"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

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

# Consistent online snapshot via the stdlib backup API.
"${PYTHON}" - "${DB_PATH}" "${DEST}" <<'PY'
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
echo "backup_db.sh: wrote ${DEST}"

# Prune backups older than the retention window.
find "${BACKUP_DIR}" -maxdepth 1 -name 'app-*.db' -type f -mtime "+${RETENTION_DAYS}" -delete
