# Agent design

The living reference for the agent stack. Full design narratives are in this
file's git history.

The one-sentence architecture: **the MCP server and the in-app loop are
consumers of `app/services/` — same write path as the UI routes, same
validation, same soft deletes, same `activity_events` audit trail — with the
destructive endpoints (purge, empty-trash) simply never exposed as tools.**

## Tool surface

Tools map 1:1 onto service functions — (validate args) → (open session) →
(call service) → (serialize). Read tools reuse the REST response models.

- **Tasks:** `list_tasks` (filtered, paginated, cap 200), `get_task` (+rollup
  and blocked flag), `create_task`, `update_task` (recurrence `edit_scope`
  supported), `complete_task` (triggers recurrence spawning), `reopen_task`,
  `trash_task` (the only delete — cascades, restorable).
- **Projects:** `list_projects`, `get_project`, `create_project`,
  `update_project`, `close_project`/`reopen_project`, `trash_project`.
- **Search/focus/trash/activity:** `search` (FTS-tiered — this *is* the
  retrieval story), `get_focus_plan`, `list_trash`,
  `restore_task`/`restore_project`, `list_activity`.
- **Stored day plan (#319):** `pin_focus_task` (floating, or fixed with
  `start_time`), `add_focus_commitment` (fixed non-task time),
  `remove_focus_entry` (soft) / `restore_focus_entry`. Entry ids come back in
  `get_focus_plan` (`entry_id` on blocks and commitments).
- **Dependencies/recurrence:** `list_dependencies`, `add_dependency`,
  `remove_dependency`, `skip_occurrence`, `stop_recurrence`. Recurrence
  creation/editing rides on `create_task`/`update_task`.

**Deliberately not exposed:** `purge_task`, `purge_project`, `empty_trash`,
`reorder_projects`, the dashboard overview. Trash retention (#321) — the
scheduled purge of trash older than `TRASH_RETENTION_DAYS` — is a lifespan job
in the API process, not a tool: nothing on the tool surface can trigger or
schedule it, and its purges are attributed to `retention`, never to an agent.

## Guardrails

1. **No hard deletes, structurally** — purge/empty-trash are never registered
   as tools, so no validation bug or prompt injection reaches them.
2. **Pydantic validation at the boundary**; service domain errors return as
   tool errors the model can self-correct from — never a 500.
3. **Attribution**: every mutation lands in `activity_events` with an actor —
   `agent:mcp`, `agent:loop`, or a recognized delegate (`agent:conductor` via
   `X-Agent-Actor`; unknown values fall back to `agent:loop`, so nobody
   stamps an arbitrary identity).
4. **Structured logging** with a per-call request ID.
5. **Rate limiting** on the agent messages endpoint (`agent_messages_per_min`).

## Transport

stdio MCP server as a module of the backend package (`python -m
app.mcp.server`), registered in the repo's `.mcp.json`; it imports services
directly and opens its own sessions (SQLite WAL handles the two processes).
The in-app loop consumes the same transport-agnostic registry
(`app/tools/registry.py`) — `ToolSpec`s are byte-identical to the MCP
`inputSchema`s (parity-tested). Streamable HTTP is deferred until a consumer
needs it.

## Runtime

Served by the workspace `../llama-swap/` stack — one proxy owning the RTX
3060, one shared `gemma-4-12b` (UD-Q4_K_XL) entry for chess + PCC. Server
flags live in `../llama-swap/config.yaml` only. `-c 131072` with `q8_0` KV:
measured 9.5 GB loaded / 10.5 GB peak on a 125k needle test; generation ~112
tok/s shallow → ~41 tok/s at depth; prefill ~446–680 tok/s; cold load ~100 s
worst case (the provider timeout tolerates it). Containers reach it via
`host.docker.internal:8200`.

## Shipped architecture (pointers)

- **Provider** `app/ai/providers/llamacpp.py`: OpenAI wire over httpx,
  Pydantic-validated, typed errors, gemma quirks handled
  (`reasoning_content` never round-trips; thinking off by default; sampling
  temp 1.0 / top-p 0.95 / top-k 64 set per request). Live smoke:
  `PCC_LLM_INTEGRATION=1 pytest tests/test_ai_llamacpp_integration.py`.
- **Loop** `app/ai/loop.py`: bounded iterations (10) + separate correction
  budget (3); schema failures are corrections, domain rejections are ordinary
  results; writes stamped `agent:loop`. Every model call carries
  `max_tokens` (`DEFAULT_MAX_TOKENS` = 2048, the context budget's completion
  reserve; moves into the model profile with #402), since llama-server's
  default is unbounded and a degenerate generation would otherwise run to the
  run budget (#397). A cut-off call (`finish_reason: "length"`) never passes
  its words on: a cut-off text turn is dropped — no reply saved, nothing
  added to history — and the run ends `truncated`; a cut-off turn's tool
  calls still run (each call's arguments arrived whole), only the text beside
  them is dropped. Work is bounded too (#398): a turn in which *every* call
  repeats an exchange the run already made — keyed on tool + canonicalised
  arguments + the answer, so a repeat that comes back different (a list
  after a write) is progress — ends the run `no_progress`; a turn with a
  schema error is left to the correction budget instead. A run dispatches at
  most `DEFAULT_MAX_TOOL_CALLS` = 24 calls (the largest deployed run made 18,
  an 8-task batch made twice after schema errors; schema-invalid calls
  count); a call past it is answered "not run" (one tool answer per call),
  never dispatched, and the run ends `tool_call_limit` after that turn.
- **Limit stops say what they did** (#399): when a run ends `max_iterations`,
  `correction_limit`, `truncated`, `no_progress` or `tool_call_limit` and at
  least one call reached the service layer (done, refused or looked up), the
  loop makes one more model call — offered no tools, capped at
  `SUMMARY_MAX_TOKENS` = 512, bounded by the time left on the run deadline —
  that reads a fresh brief, not the run's transcript: the user's message, a
  code-built record of the run (`app/ai/run_record.py`, sorted from the
  `ToolCallRecord`s into Done / Refused / Looked up / Not run, one compact line
  per call, "Done: nothing" said outright) and the note "Whatever the record
  above does not show as done was not done." Its text is saved as the reply
  (and so enters the next turn's history); the stop reason is unchanged, so
  the UI still shows its stop line and delegates still read the limit. Reads
  are `READ_TOOLS` — exactly the registry bodies opened with
  `tool_session(..., write=False)`, pinned by a test; any other tool that
  answers is "done". No call ran, a Stop, or a summary that fails, times out,
  is cut off (`finish_reason: "length"`) or comes back empty or as a tool call:
  no reply, as before. `cancelled` and the failure stops never summarise.
- **Stop reasons**: `completed`, `max_iterations`, `correction_limit`,
  `truncated`, `no_progress`, `tool_call_limit` and `cancelled` return 200; `provider_error` (502), `timed_out` (504) and
  `internal_error` (500) are failures. Every one persists a truthful assistant
  turn. The deadline and the cancel event (`POST …/cancel`, #324,
  `app/api/run_cancel.py`) are checked at the same two boundaries — before each
  provider call and each tool dispatch — so neither interrupts a running tool;
  an in-flight provider call finishes first, and calls it proposed after Stop
  are recorded as not run. The cancel event is per run (registered inside the
  run lock), so a late Stop never leaks into the next run.
- **Prompt**: app base contract + vendored global Glitch
  (`app/ai/personality-global.md`, canonical in `../agent-standard/`; fix
  drift by re-copying) + today's date. PCC ships no app-flavor layer.
- **Persistence** (`conversations`, `conversation_messages`): assistant turns
  carry the tool trajectory + stop_reason; the user turn commits before the
  loop runs. `POST /api/agent/conversations/{id}/messages` is the one
  model-calling endpoint, synchronous by decision (SSE only if the wait feels
  bad).
- **Chat panel** `features/agent/`: visible trajectory, per-mutation undo,
  ambient entry from the search bar.

## Context budget (`app/ai/context_budget.py`)

A deterministic recent window over history — no summarizer (that's a second
GPU call; cloud is forbidden). Token estimate is `CHARS_PER_TOKEN = 3`
(deliberately over-counts, so budgets are floors). Reserves off the 131,072
window: system prompt + schemas 8,192; current turn 2,667; run tool traffic
16,384; completion 2,048. The binding constraint is prefill *time*, not the
window: `PREFILL_BUDGET_TOKENS = 24_576` (~55 s pessimistic against the 240 s
run budget) is the effective history ceiling.
`services/conversations.py::history_for_loop` is the source of truth; the
loop re-applies the same function with measured overhead before every
request. Conversation GET is paginated (`before_id`, default 100/max 500).

## Eval harness + baseline

```bash
cd backend
PCC_AGENT_EVALS=1 .venv/bin/pytest tests/test_agent_evals.py -v -s
```

Opt-in; scripted scenarios through the full loop + registry against the real
runtime. Asserts trajectory *shape* + DB end-state + audit invariants, never
exact sequences (temp 1.0). **Gate: prompt/model/loop changes must not
regress this baseline.**

`PCC_AGENT_EVALS_RUNS=N` (default 1) repeats every scenario N times — each run
is its own pytest case with a fresh DB, so the gate is "all N pass", matching
the "N consecutive suites" baseline below. With N > 1 and `-s`, an end-of-run
table gives each scenario's pass rate (k/N), min–max iterations and min–max
duration (the first call can include a cold model load). Aggregation is in
`tests/eval_report.py`, unit-tested in CI without a GPU.

**Baseline (gemma-4-12b UD-Q4_K_XL, 2026-07-11, 4 consecutive suites, 24/24;
re-confirmed under the layered personality 2026-07-11, the context budget
2026-08-05, and llama-swap serving 2026-10-18 — 4 consecutive suites, 28/28
with `delegate_actor` added):**

| Scenario | Asserts | Iterations | Warm time |
| --- | --- | --- | --- |
| `create_task_with_fields` | routing, priority, date math, audit | 3–5 | 3.0–5.9 s |
| `find_and_complete` | described task found via read, completed | 3 | 1.2–1.8 s |
| `reschedule` | targeted due-date update only (ISO target relative to today, #380) | 3 | 1.5–4.5 s |
| `delete_is_soft` | lands in trash, restorable, audited | 3 | 1.0–3.2 s |
| `read_only_count` | exact count (the only number in the reply, numeral or word, #381), zero mutations | 3 | 1.4–2.0 s |
| `honest_about_missing` | nothing invented or acted on; frugality tripwire ≤7 iterations / ≤6 reads | 2–5 | 0.9–2.3 s |
| `delegate_actor` | via the route with `X-Agent-Actor: agent:conductor`: every run event the delegate's, prior events untouched | 3–5 | 1.5–3.2 s |

Standing observations: self-correction pays for itself (the recurring gemma
`name`/`title` miss always fixes on feedback); FTS5 retrieval is sufficient —
this table is the tripwire for ever adding embeddings; `honest_about_missing`
no longer sweeps exhaustively before conceding (pre-llama-swap: up to 10
turns, per-project lists, the trash) — under llama-swap it's `search` then at
most three `list_tasks`/`list_trash` (32 runs, 2026-10-18). The scenario now
asserts ≤7 iterations and ≤6 read calls (#329): 2 of headroom over the
observed maximum, and well short of the old sweep.

## Deferred

Embeddings/`sqlite-vec` (only if retrieval regresses) · SSE streaming (only
if the synchronous wait feels bad).
