#!/usr/bin/env bash
# Bootstraps env/deps, runs migrations, and starts the backend + frontend
# dev servers.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
BACKEND_VENV="$BACKEND_DIR/.venv"
BACKEND_PYTHON="$BACKEND_VENV/bin/python"
SYSTEM_PYTHON="${PYTHON:-python3}"

declare -a CHILD_PIDS=()

source "$ROOT_DIR/scripts/dependencies.sh"

log() {
  printf '[main] %s\n' "$*"
}

die() {
  printf '[main] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  local command_name="$1"
  command -v "$command_name" >/dev/null 2>&1 || die "Missing required command: $command_name"
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM

  if [ "${#CHILD_PIDS[@]}" -gt 0 ]; then
    log "Stopping dev processes..."
    for pid in "${CHILD_PIDS[@]}"; do
      if kill -0 "$pid" >/dev/null 2>&1; then
        kill "$pid" >/dev/null 2>&1 || true
      fi
    done
    wait "${CHILD_PIDS[@]}" >/dev/null 2>&1 || true
  fi

  exit "$status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

copy_env_if_missing() {
  local target="$1"
  local example="$2"

  if [ ! -f "$target" ]; then
    [ -f "$example" ] || die "Cannot create $target because $example is missing."
    cp "$example" "$target"
    log "Created ${target#$ROOT_DIR/} from ${example#$ROOT_DIR/}."
  fi
}

read_env_value() {
  local env_file="$1"
  local key="$2"

  "$SYSTEM_PYTHON" - "$env_file" "$key" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

env_path = Path(sys.argv[1])
target = sys.argv[2]

if not env_path.exists():
    raise SystemExit(0)

for raw_line in env_path.read_text().splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() != target:
        continue
    value = value.strip().strip("'").strip('"')
    print(value)
    break
PY
}

port_is_open() {
  local host="$1"
  local port="$2"

  "$SYSTEM_PYTHON" - "$host" "$port" <<'PY'
from __future__ import annotations

import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(1)
    raise SystemExit(0 if sock.connect_ex((host, port)) == 0 else 1)
PY
}

http_ok() {
  local url="$1"

  "$SYSTEM_PYTHON" - "$url" <<'PY'
from __future__ import annotations

import sys
import urllib.request

url = sys.argv[1]

try:
    with urllib.request.urlopen(url, timeout=1) as response:
        raise SystemExit(0 if 200 <= response.status < 300 else 1)
except Exception:
    raise SystemExit(1)
PY
}

wait_for_http() {
  local name="$1"
  local url="$2"
  local timeout_seconds="$3"
  local elapsed=0

  while [ "$elapsed" -lt "$timeout_seconds" ]; do
    if http_ok "$url"; then
      log "$name is ready."
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done

  die "$name did not become ready at $url within ${timeout_seconds}s."
}

preflight_ports() {
  if port_is_open 127.0.0.1 8101; then
    die "Port 8101 is already in use. Stop the existing backend process, then rerun ./main.sh."
  fi

  if port_is_open 127.0.0.1 5173; then
    die "Port 5173 is already in use. Stop the existing frontend process, then rerun ./main.sh."
  fi
}

run_migrations() {
  log "Applying database migrations."
  (cd "$BACKEND_DIR" && "$BACKEND_VENV/bin/alembic" upgrade head)
}

start_backend() {
  log "Starting backend on port 8101."
  (cd "$BACKEND_DIR" && "$BACKEND_PYTHON" -m app.main) &
  CHILD_PIDS+=("$!")
  wait_for_http "Backend" "http://127.0.0.1:8101/health" 30
}

start_frontend() {
  log "Starting frontend on port 5173."
  (cd "$FRONTEND_DIR" && npm run dev -- --strictPort) &
  CHILD_PIDS+=("$!")
  wait_for_http "Frontend" "http://127.0.0.1:5173/" 30
}

main() {
  copy_env_if_missing "$BACKEND_DIR/.env" "$BACKEND_DIR/.env.example"
  copy_env_if_missing "$FRONTEND_DIR/.env" "$FRONTEND_DIR/.env.example"
  ensure_backend_deps
  ensure_frontend_deps
  preflight_ports
  run_migrations
  start_backend
  start_frontend

  log "Dev stack is running."
  log "Frontend: http://127.0.0.1:5173"
  log "Backend:  http://127.0.0.1:8101"
  log "Press Ctrl-C to stop."

  set +e
  wait -n "${CHILD_PIDS[@]}"
  local status=$?
  set -e

  if [ "$status" -ne 130 ] && [ "$status" -ne 143 ]; then
    log "A dev process exited; shutting down the rest."
  fi
  return "$status"
}

main "$@"
