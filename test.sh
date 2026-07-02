#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
BACKEND_VENV="$BACKEND_DIR/.venv"
BACKEND_PYTHON="$BACKEND_VENV/bin/python"
SYSTEM_PYTHON="${PYTHON:-python3}"
INCLUDE_AI_EVALS=0
FAILURES=0

log() {
  printf '[test] %s\n' "$*"
}

die() {
  printf '[test] ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: ./test.sh [--ai-evals]

Runs the default local quality gate:
  backend pytest, ruff, mypy
  frontend Vitest, lint, build

Options:
  --ai-evals   Also run the Ollama-backed AI eval suites.
  -h, --help   Show this help.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --ai-evals)
      INCLUDE_AI_EVALS=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "Unknown argument: $arg"
      ;;
  esac
done

require_command() {
  local command_name="$1"
  command -v "$command_name" >/dev/null 2>&1 || die "Missing required command: $command_name"
}

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

run_check() {
  local label="$1"
  local workdir="$2"
  shift 2

  log "Running $label."
  set +e
  (cd "$workdir" && "$@")
  local status=$?
  set -e

  if [ "$status" -eq 0 ]; then
    log "PASS $label."
  else
    log "FAIL $label (exit $status)."
    FAILURES=1
  fi
}

run_quality_gate() {
  run_check "backend pytest" "$BACKEND_DIR" "$BACKEND_VENV/bin/pytest"
  run_check "backend ruff" "$BACKEND_DIR" "$BACKEND_VENV/bin/ruff" check .
  run_check "backend mypy" "$BACKEND_DIR" "$BACKEND_VENV/bin/mypy" app tests
  run_check "frontend vitest" "$FRONTEND_DIR" npm run test -- --no-file-parallelism
  run_check "frontend lint" "$FRONTEND_DIR" npm run lint
  run_check "frontend build" "$FRONTEND_DIR" npm run build
}

run_ai_evals() {
  if ! http_ok "http://localhost:11434/api/tags"; then
    log "FAIL AI eval preflight: Ollama is not reachable at http://localhost:11434."
    FAILURES=1
    return 0
  fi

  run_check "AI eval task_extraction" "$BACKEND_DIR" "$BACKEND_PYTHON" -m app.ai.evals.run_evals
  run_check "AI eval project_matching" "$BACKEND_DIR" "$BACKEND_PYTHON" -m app.ai.evals.run_match_evals
  run_check "AI eval summary" "$BACKEND_DIR" "$BACKEND_PYTHON" -m app.ai.evals.run_summary_evals
  run_check "AI eval break_down_task" "$BACKEND_DIR" "$BACKEND_PYTHON" -m app.ai.evals.run_breakdown_evals
}

main() {
  copy_env_if_missing "$BACKEND_DIR/.env" "$BACKEND_DIR/.env.example"
  copy_env_if_missing "$FRONTEND_DIR/.env" "$FRONTEND_DIR/.env.example"
  ensure_backend_deps
  ensure_frontend_deps
  run_quality_gate

  if [ "$INCLUDE_AI_EVALS" -eq 1 ]; then
    run_ai_evals
  fi

  if [ "$FAILURES" -eq 0 ]; then
    log "All requested checks passed."
  else
    die "One or more checks failed."
  fi
}

main
