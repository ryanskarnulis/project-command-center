#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
BACKEND_VENV="$BACKEND_DIR/.venv"
BACKEND_PYTHON="$BACKEND_VENV/bin/python"
SYSTEM_PYTHON="${PYTHON:-python3}"

declare -a CHILD_PIDS=()

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

check_python_version() {
  "$SYSTEM_PYTHON" - <<'PY'
import sys

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ is required.")
PY
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
  require_command "$SYSTEM_PYTHON"
  check_python_version

  if [ ! -x "$BACKEND_PYTHON" ]; then
    log "Creating backend virtualenv."
    "$SYSTEM_PYTHON" -m venv "$BACKEND_VENV"
  fi

  if backend_tools_missing; then
    log "Installing backend dependencies."
    (cd "$BACKEND_DIR" && "$BACKEND_PYTHON" -m pip install -e '.[dev]' -c requirements.lock)
  fi
}

ensure_frontend_deps() {
  require_command npm

  if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    log "Installing frontend dependencies."
    (cd "$FRONTEND_DIR" && npm ci)
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
  if port_is_open 127.0.0.1 8000; then
    die "Port 8000 is already in use. Stop the existing backend process, then rerun ./main.sh."
  fi

  if port_is_open 127.0.0.1 5173; then
    die "Port 5173 is already in use. Stop the existing frontend process, then rerun ./main.sh."
  fi
}

ensure_ollama() {
  if http_ok "http://localhost:11434/api/tags"; then
    log "Ollama is already running."
    return 0
  fi

  require_command ollama
  log "Starting Ollama."
  ollama serve &
  CHILD_PIDS+=("$!")
  wait_for_http "Ollama" "http://localhost:11434/api/tags" 30
}

run_migrations() {
  log "Applying database migrations."
  (cd "$BACKEND_DIR" && "$BACKEND_VENV/bin/alembic" upgrade head)
}

start_backend() {
  log "Starting backend on port 8000."
  (cd "$BACKEND_DIR" && "$BACKEND_PYTHON" -m app.main) &
  CHILD_PIDS+=("$!")
  wait_for_http "Backend" "http://127.0.0.1:8000/health" 30
}

start_frontend() {
  log "Starting frontend on port 5173."
  (cd "$FRONTEND_DIR" && npm run dev -- --strictPort) &
  CHILD_PIDS+=("$!")
  wait_for_http "Frontend" "http://127.0.0.1:5173/" 30
}

start_discord_if_configured() {
  local token
  local secret

  token="$(read_env_value "$BACKEND_DIR/.env" "DISCORD_BOT_TOKEN")"
  secret="$(read_env_value "$BACKEND_DIR/.env" "BACKEND_SHARED_SECRET")"

  if [ -n "$token" ] && [ -n "$secret" ]; then
    log "Starting Discord bot."
    (cd "$BACKEND_DIR" && "$BACKEND_PYTHON" -m app.integrations.discord.bot) &
    CHILD_PIDS+=("$!")
  else
    log "Skipping Discord bot; set DISCORD_BOT_TOKEN and BACKEND_SHARED_SECRET in backend/.env to enable it."
  fi
}

main() {
  copy_env_if_missing "$BACKEND_DIR/.env" "$BACKEND_DIR/.env.example"
  copy_env_if_missing "$FRONTEND_DIR/.env" "$FRONTEND_DIR/.env.example"
  ensure_backend_deps
  ensure_frontend_deps
  preflight_ports
  run_migrations
  ensure_ollama
  start_backend
  start_frontend
  start_discord_if_configured

  log "Dev stack is running."
  log "Frontend: http://127.0.0.1:5173"
  log "Backend:  http://127.0.0.1:8000"
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
