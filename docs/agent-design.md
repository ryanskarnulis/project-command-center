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
- **Dependencies/recurrence:** `list_dependencies`, `add_dependency`,
  `remove_dependency`, `skip_occurrence`, `stop_recurrence`. Recurrence
  creation/editing rides on `create_task`/`update_task`.

**Deliberately not exposed:** `purge_task`, `purge_project`, `empty_trash`,
`reorder_projects`, the dashboard overview.

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
  results; writes stamped `agent:loop`.
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

**Baseline (gemma-4-12b UD-Q4_K_XL, 2026-07-11, 4 consecutive suites, 24/24;
re-confirmed under the layered personality 2026-07-11 and the context budget
2026-08-05):**

| Scenario | Asserts | Iterations | Warm time |
| --- | --- | --- | --- |
| `create_task_with_fields` | routing, priority, date math, audit | 3–5 | 3.0–5.9 s |
| `find_and_complete` | described task found via read, completed | 3 | 1.2–1.8 s |
| `reschedule` | targeted due-date update only | 3 | 1.5–4.5 s |
| `delete_is_soft` | lands in trash, restorable, audited | 3 | 1.0–3.2 s |
| `read_only_count` | correct count, zero mutations | 3 | 1.4–2.0 s |
| `honest_about_missing` | nothing invented or acted on | 4–10 | 2.2–4.1 s |

Standing observations: self-correction pays for itself (the recurring gemma
`name`/`title` miss always fixes on feedback); FTS5 retrieval is sufficient —
this table is the tripwire for ever adding embeddings; `honest_about_missing`
over-searches before conceding (the frugality tripwire in `TODO.md`).

## Deferred

Embeddings/`sqlite-vec` (only if retrieval regresses) · SSE streaming (only
if the synchronous wait feels bad).
