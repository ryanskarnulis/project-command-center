#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
BACKEND_VENV="$BACKEND_DIR/.venv"
BACKEND_PYTHON="$BACKEND_VENV/bin/python"
SYSTEM_PYTHON="${PYTHON:-python3}"
FAILURES=0

source "$ROOT_DIR/scripts/dependencies.sh"

log() {
  printf '[test] %s\n' "$*"
}

die() {
  printf '[test] ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: ./test.sh

Runs the local quality gate:
  backend pytest, ruff, mypy
  frontend Vitest, lint, build

Options:
  -h, --help   Show this help.
EOF
}

for arg in "$@"; do
  case "$arg" in
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

main() {
  copy_env_if_missing "$BACKEND_DIR/.env" "$BACKEND_DIR/.env.example"
  copy_env_if_missing "$FRONTEND_DIR/.env" "$FRONTEND_DIR/.env.example"
  ensure_backend_deps
  ensure_frontend_deps
  run_quality_gate

  if [ "$FAILURES" -eq 0 ]; then
    log "All requested checks passed."
  else
    die "One or more checks failed."
  fi
}

main
