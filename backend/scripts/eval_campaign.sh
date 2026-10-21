#!/usr/bin/env bash
# Harness-level A/B for an agent change: alternating blocks of five between two
# trees on ONE llama-server, so server drift lands on both arms alike (#410).
#
#   backend/scripts/eval_campaign.sh --a /path/to/main --b /path/to/branch \
#       --k 'create_task or delete_is_soft' --blocks 4 [--fresh-per-block]
#
# An arm may also be an environment setting on the same tree, for a knob the
# app reads from the environment:
#
#   backend/scripts/eval_campaign.sh --a . --b . --env-b LLAMACPP_MODEL=other --k ...
#
# Each block runs `tests/test_agent_evals.py -k <expr>` once per arm from that
# arm's own `backend/` directory (so its own `app` and its own copy of the
# harness are what is measured), with PCC_AGENT_EVALS_RUNS=<runs> and
# PCC_AGENT_EVALS_MAX_RUNS=<runs> so a block is exactly <runs> scored samples
# (no escalation: escalation would give the arms different sample counts).
# Before each arm-block the log records the tree's HEAD, the sha of its
# test_agent_evals.py, `app.__file__` as Python resolves it (a gate that cannot
# prove which tree it measured measured nothing), and llama-swap /running. The
# arm order flips every block.
#
# Two trees with different harness files are not a fair A/B on any scenario the
# difference touches; the script says so when the shas differ.
#
# Arms are paired: block <n> of both arms runs with
# PCC_AGENT_EVALS_SEED=<seed> + 1000*<n>, so sample j of each arm's block n
# draws the same sampling stream, and the joined table gets a paired column
# (discordant pairs and the exact McNemar p). --seed <base> picks the base
# (default 410); --no-seed runs unseeded. Pairing is approximate (llama.cpp is
# not bit-reproducible) and does not replace the flipped block order.
#
# --fresh-per-block calls llama-swap /unload before each arm-block, so every
# block starts from a cold model. It does not check that the server is idle:
# only use it when the server is yours for the duration.
#
# Output lands in --out (default $TMPDIR/pcc-campaign-<utc>/, never the repo)
# as block-<n>-<arm>.jsonl plus .out and campaign.log; the last thing printed
# is the joined table (scripts/campaign_report.py).
#
# Python: $PYTHON, else this checkout's backend/.venv, else `python` on PATH.
# Never compare arms run in separate batches: docs/agent-campaigns.md.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$(cd "$HERE/.." && pwd)"
PYTHON="${PYTHON:-$BACKEND/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON="$(command -v python)"

A=""; B=""; K=""; BLOCKS=4; FRESH=0; OUT=""; RUNS=5; SEED=410
ENV_A=(); ENV_B=()
BASE_URL="${LLAMACPP_BASE_URL:-http://127.0.0.1:8200/v1}"
while [ $# -gt 0 ]; do
  case "$1" in
    --a) A="$2"; shift 2 ;;
    --b) B="$2"; shift 2 ;;
    --k) K="$2"; shift 2 ;;
    --env-a) ENV_A+=("$2"); shift 2 ;;
    --env-b) ENV_B+=("$2"); shift 2 ;;
    --blocks) BLOCKS="$2"; shift 2 ;;
    --runs) RUNS="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --fresh-per-block) FRESH=1; shift ;;
    --seed) SEED="$2"; shift 2 ;;
    --no-seed) SEED=""; shift ;;
    -h|--help) sed -n '2,41p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$A" ] && [ -n "$B" ] && [ -n "$K" ] || { echo "need --a, --b and --k" >&2; exit 2; }
A="$(cd "$A" && pwd)"; B="$(cd "$B" && pwd)"
[ -f "$A/backend/tests/test_agent_evals.py" ] || { echo "$A is not a PCC tree" >&2; exit 2; }
[ -f "$B/backend/tests/test_agent_evals.py" ] || { echo "$B is not a PCC tree" >&2; exit 2; }
OUT="${OUT:-${TMPDIR:-/tmp}/pcc-campaign-$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$OUT"
LOG="$OUT/campaign.log"
SWAP_ROOT="${BASE_URL%/v1}"

log() { echo "$*" | tee -a "$LOG"; }

arm_env() {  # $1 = arm name → that arm's KEY=VALUE settings, one per line
  if [ "$1" = a ]; then printf '%s\n' "${ENV_A[@]+"${ENV_A[@]}"}"; else printf '%s\n' "${ENV_B[@]+"${ENV_B[@]}"}"; fi
}

declare -A HARNESSES

identity() {  # $1 = arm name, $2 = tree; records the harness sha in HARNESSES
  local head file running
  head="$(git -C "$2" rev-parse --short HEAD)"
  HARNESSES["$1"]="$(sha256sum "$2/backend/tests/test_agent_evals.py" | cut -c1-12)"
  file="$(cd "$2/backend" && "$PYTHON" -c 'import app; print(app.__file__)')"
  running="$(curl -s -m 5 "$SWAP_ROOT/running" || echo '{}')"
  log "  arm $1: tree $2 HEAD $head harness ${HARNESSES[$1]} app $file env [$(arm_env "$1" | tr '\n' ' ')] running $running"
  case "$file" in "$2"/*) ;; *) log "  !! app resolved outside $2; aborting"; exit 1 ;; esac
}

fresh() {
  curl -s -m 10 "$SWAP_ROOT/unload" >/dev/null || { log "  unload failed; aborting"; exit 1; }
  log "  unloaded $(date -u +%H:%M:%SZ); the first request reloads"
}

run_block() {  # $1 = block number, $2 = arm name, $3 = tree
  local report="$OUT/block-$1-$2.jsonl"
  # Same block, same seeds, either arm (`evalstats.sample_seed`).
  local seed=""
  [ -n "$SEED" ] && seed=$((SEED + 1000 * $1))
  log "block $1 arm $2 start $(date -u +%H:%M:%SZ) seed ${seed:-none}"
  if [ "$FRESH" = 1 ]; then fresh; fi
  identity "$2" "$3"
  local -a extra=()
  while IFS= read -r kv; do [ -n "$kv" ] && extra+=("$kv"); done < <(arm_env "$2")
  # A block below its floor exits non-zero; the report is still what we came for.
  (cd "$3/backend" && env "${extra[@]+"${extra[@]}"}" PCC_AGENT_EVALS=1 \
     PCC_AGENT_EVALS_RUNS="$RUNS" PCC_AGENT_EVALS_MAX_RUNS="$RUNS" \
     PCC_AGENT_EVALS_REPORT="$report" PCC_AGENT_EVALS_SEED="$seed" \
     LLAMACPP_BASE_URL="$BASE_URL" \
     "$PYTHON" -m pytest tests/test_agent_evals.py -k "$K" -s 2>&1 \
       | tee -a "$OUT/block-$1-$2.out" \
       | grep -E '^\[eval\] scenario=.*PASS_RATE|passed|failed' || true)
  log "block $1 arm $2 end $(date -u +%H:%M:%SZ)"
  SPECS+=("$2=$report")
}

SPECS=()
log "campaign $(date -u +%FT%TZ) k='$K' blocks=$BLOCKS runs=$RUNS fresh=$FRESH seed=${SEED:-none} out=$OUT"
for ((i = 1; i <= BLOCKS; i++)); do
  if (( i % 2 )); then run_block "$i" a "$A"; run_block "$i" b "$B"
  else run_block "$i" b "$B"; run_block "$i" a "$A"; fi
done
if [ "${HARNESSES[a]}" != "${HARNESSES[b]}" ]; then
  log "!! the arms ran different test_agent_evals.py files (${HARNESSES[a]} vs ${HARNESSES[b]}): scenarios whose checks differ are not comparable"
fi
log ""
(cd "$BACKEND" && "$PYTHON" scripts/campaign_report.py "${SPECS[@]}") | tee -a "$LOG"
