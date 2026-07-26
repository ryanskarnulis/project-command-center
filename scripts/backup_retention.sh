#!/usr/bin/env bash
#
# Retention helpers for scripts/backup_db.sh.
#
# Kept in its own file so the validation and pruning rules can be exercised
# directly against a throwaway directory (see backend/tests/test_backup_retention.py)
# without ever running a real backup against data/.
#
# Source it; don't execute it:
#   source "${SCRIPT_DIR}/backup_retention.sh"

# Accept only non-negative decimal integers. GNU find happily accepts a negative
# `-mtime +N`, where it matches *every* file — so an unvalidated typo such as
# BACKUP_RETENTION_DAYS=-1 would delete the entire backup history, including the
# snapshot just written. Fractional and non-numeric values are equally unsafe.
validate_retention_days() {
  local value="${1-}"

  if [[ ! "${value}" =~ ^[0-9]+$ ]]; then
    echo "backup_db.sh: BACKUP_RETENTION_DAYS must be a non-negative integer (got: '${value}')" >&2
    return 1
  fi

  return 0
}

# Delete snapshots older than the retention window. Validates first: on invalid
# input nothing is deleted and the caller sees a non-zero exit.
prune_backups() {
  local backup_dir="${1-}"
  local retention_days="${2-}"

  validate_retention_days "${retention_days}" || return 1

  find "${backup_dir}" -maxdepth 1 -name 'app-*.db' -type f -mtime "+${retention_days}" -delete
}
