#!/usr/bin/env bash
# Shared dependency bootstrap for main.sh and test.sh. The caller defines the
# project paths plus log() and require_command().

dependency_fingerprint() {
  "$SYSTEM_PYTHON" - "$@" <<'PY'
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

digest = hashlib.sha256()
for raw_path in sys.argv[1:]:
    path = Path(raw_path)
    payload = path.read_bytes()
    digest.update(path.name.encode())
    digest.update(b"\0")
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)
print(digest.hexdigest())
PY
}

dependency_stamp_matches() {
  local stamp_path="$1"
  local expected="$2"
  local actual=""
  [ -f "$stamp_path" ] || return 1
  IFS= read -r actual < "$stamp_path" || return 1
  [ "$actual" = "$expected" ]
}

write_dependency_stamp() {
  local stamp_path="$1"
  local fingerprint="$2"
  local temporary_stamp="${stamp_path}.tmp.$$"
  printf '%s\n' "$fingerprint" > "$temporary_stamp"
  mv "$temporary_stamp" "$stamp_path"
}

check_python_version() {
  "$SYSTEM_PYTHON" - <<'PY'
import sys

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ is required.")
PY
}

# A venv's bin/python is an unversioned symlink to the system interpreter, but
# its packages live in a version-specific lib/pythonX.Y tree. When the OS
# upgrades python3 the symlink still resolves and every console script stays
# executable, yet the new interpreter looks for a site-packages directory that
# does not exist and every import fails. pyvenv.cfg records the version the
# tree was built for, so compare it against what bin/python actually is now.
backend_venv_matches_interpreter() {
  local config_path="$BACKEND_VENV/pyvenv.cfg"

  [ -x "$BACKEND_PYTHON" ] || return 1
  [ -f "$config_path" ] || return 1

  "$BACKEND_PYTHON" - "$config_path" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

recorded = ""
for raw_line in Path(sys.argv[1]).read_text().splitlines():
    key, separator, value = raw_line.partition("=")
    if separator and key.strip() in {"version", "version_info"}:
        recorded = value.strip()
        break

running = "%d.%d" % sys.version_info[:2]
raise SystemExit(0 if ".".join(recorded.split(".")[:2]) == running else 1)
PY
}

# Reinstalling into a venv whose interpreter moved cannot repair it: pip would
# land packages in the tree the current interpreter already ignores.
rebuild_backend_venv() {
  [ -n "${BACKEND_VENV:-}" ] || return 1
  rm -rf "$BACKEND_VENV"
  "$SYSTEM_PYTHON" -m venv "$BACKEND_VENV"
}

backend_tools_missing() {
  [ ! -x "$BACKEND_PYTHON" ] && return 0
  [ ! -x "$BACKEND_VENV/bin/alembic" ] && return 0
  [ ! -x "$BACKEND_VENV/bin/pytest" ] && return 0
  [ ! -x "$BACKEND_VENV/bin/ruff" ] && return 0
  [ ! -x "$BACKEND_VENV/bin/mypy" ] && return 0
  return 1
}

ensure_backend_deps() {
  local stamp_path="$BACKEND_VENV/.pcc-deps.sha256"
  local fingerprint

  require_command "$SYSTEM_PYTHON"
  check_python_version
  fingerprint="$(dependency_fingerprint \
    "$BACKEND_DIR/pyproject.toml" \
    "$BACKEND_DIR/requirements.lock")"

  if [ ! -d "$BACKEND_VENV" ]; then
    log "Creating backend virtualenv."
    "$SYSTEM_PYTHON" -m venv "$BACKEND_VENV"
  elif ! backend_venv_matches_interpreter; then
    log "Backend virtualenv no longer matches its Python; rebuilding it."
    rebuild_backend_venv
  fi

  if backend_tools_missing || ! dependency_stamp_matches "$stamp_path" "$fingerprint"; then
    log "Installing backend dependencies."
    (cd "$BACKEND_DIR" && "$BACKEND_PYTHON" -m pip install -e '.[dev]' -c requirements.lock) \
      || return
    write_dependency_stamp "$stamp_path" "$fingerprint"
  fi
}

ensure_frontend_deps() {
  local stamp_path="$FRONTEND_DIR/node_modules/.pcc-deps.sha256"
  local fingerprint

  require_command npm
  fingerprint="$(dependency_fingerprint \
    "$FRONTEND_DIR/package.json" \
    "$FRONTEND_DIR/package-lock.json")"

  if [ ! -d "$FRONTEND_DIR/node_modules" ] \
    || ! dependency_stamp_matches "$stamp_path" "$fingerprint"; then
    log "Installing frontend dependencies."
    (cd "$FRONTEND_DIR" && npm ci) || return
    write_dependency_stamp "$stamp_path" "$fingerprint"
  fi
}
