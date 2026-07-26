# Agent design (Phase 2)

The living design record of the shipped Phase 2 agent stack: the MCP server,
the shared llama.cpp runtime, the provider layer, the tool registry + agent
loop, the layered personality, conversation persistence + the agent REST API,
the chat panel, delegate attribution, and the eval harness with its recorded
baseline. Sections carry their decision/landed dates; what remains gated
lives under "Explicitly deferred" at the end.

The one-sentence architecture: **the MCP server is a third consumer of
`app/services/` — same write path as the UI routes, same validation, same soft
deletes, same `activity_events` audit trail — with the destructive endpoints
(purge, empty-trash) simply never exposed as tools.**

## Tool surface

Tools map 1:1 onto existing service functions. No new business logic lives in
the MCP layer; a tool is (validate args) → (open session) → (call service) →
(serialize result). Read tools return the same shapes as the REST API
(reusing the Pydantic response models in `app/schemas/`).

### Tasks

| Tool | Service call | Notes |
| --- | --- | --- |
| `list_tasks` | `tasks.list_tasks` | Filters: `project_id`, `workflow_status`, `exclude_done`, `top_level_only`; paginated (`limit` default 50, cap 200 — the agent never needs an unbounded read). |
| `get_task` | `tasks.get_task` (+ `tasks.get_rollup`, `task_dependencies.is_blocked`) | One task with subtask rollup and blocked flag. |
| `create_task` | `tasks.create_task` | `project_id`, `title`, `description`, `priority`, `due_date`, `parent_task_id`, `estimated_minutes`. `project_id` omitted → default project (same as the UI). |
| `update_task` | `tasks.update_task` | Partial update; inherits the service's cycle/derived-status/blocked checks. Recurrence `edit_scope` supported. |
| `complete_task` | `tasks.mark_done` | Distinct from `update_task` — the service rejects direct `workflow_status` writes on parents and blocked tasks, and completion triggers recurrence spawning. |
| `reopen_task` | `tasks.reopen_task` | |
| `trash_task` | `tasks.soft_delete_task` | The only delete the agent gets. Cascades over the subtask tree; undone with `restore_task(..., restore_subtasks=True)`. |

### Projects

| Tool | Service call | Notes |
| --- | --- | --- |
| `list_projects` | `projects.list_projects` | `include_closed` flag. |
| `get_project` | `projects.get_project` | |
| `create_project` | `projects.create_project` | |
| `update_project` | `projects.update_project` | |
| `close_project` / `reopen_project` | `projects.close_project` / `reopen_project` | |
| `trash_project` | `projects.soft_delete_project` | Soft-deletes the subtree, like the UI. |

### Search, focus, trash, activity

| Tool | Service call | Notes |
| --- | --- | --- |
| `search` | `search.search` | FTS-tiered search over active projects/tasks. This is also the seed of the "agentic retrieval over FTS5" plan in `TODO.md` — no separate RAG tool needed yet. |
| `get_focus_plan` | `focus.get_focus_plan` | `target_date` (default today), `start_time`, `available_minutes`. Read-only. |
| `list_trash` | `task_trash.list_deleted_tasks` + `projects.list_deleted_projects` | |
| `restore_task` / `restore_project` | `task_trash.restore_task` (or `restore_task_subtree` with `restore_subtasks=True`) / `projects.restore_project` | Undo path for every agent delete. `restore_subtasks` brings back exactly the subtasks that cascade-trashed with the task, leaving separately-trashed ones alone. |
| `list_activity` | `activity.list_events` | Lets the agent read its own (and the user's) audit trail. |

### Dependencies and recurrence

| Tool | Service call | Notes |
| --- | --- | --- |
| `list_dependencies` | `task_dependencies.list_dependencies` / `list_dependents` | One tool, both directions in the result. |
| `add_dependency` | `task_dependencies.add_dependency` | Cycle/self/duplicate errors surface as tool errors. |
| `remove_dependency` | `task_dependencies.remove_dependency` | |
| `skip_occurrence` | `task_recurrence.skip_occurrence` | |
| `stop_recurrence` | `task_recurrence.stop_recurrence` | |

Recurrence *creation/editing* rides on `create_task`/`update_task`
(`recurrence` is a task field), matching the REST API.

**Deliberately not exposed:** `purge_task`, `purge_project`,
`trash.empty_trash`, `reorder_projects` (pure UI concern), and the dashboard
overview (an MCP client can compose it from `list_projects` + `list_tasks`;
add later if it earns its keep).

Slice 3 landed the first pass (task CRUD + complete, project CRUD, search,
focus, trash/restore); the dependencies and recurrence tools above shipped as
the fast follow-up (2026-07-11), which also closed a pre-existing audit gap:
`add_dependency`/`remove_dependency` now record `dependency_added`/
`dependency_removed` events in `activity_events` from every caller, UI
included.

## Guardrails

1. **No hard deletes, structurally.** The purge/empty-trash services are
   never registered as tools, so there is no argument-validation bug or
   prompt-injection path that reaches them. Every agent delete is a soft
   delete, restorable from the trash.
2. **Pydantic validation at the boundary.** Each tool's arguments are a
   Pydantic v2 model (reusing `app/schemas/` request models where they exist).
   Invalid arguments are rejected with a structured error before any session
   is opened — no best-effort parsing. Service-layer domain errors
   (`DependencyCycleError`, `BlockedTaskError`, `DerivedStatusError`, …)
   are caught and returned as tool errors with the service's message, so the
   model can self-correct instead of the server 500ing.
3. **Attribution in `activity_events`.** The table currently has no actor
   column — every event is implicitly the user. Slice 3 adds a nullable
   `actor` column (Alembic migration): `NULL` means the user (no backfill
   needed, existing rows stay correct), and the MCP server stamps
   `"agent:mcp"` (the future in-app loop will stamp its own value).
   `record_event` grows an `actor: str | None = None` keyword; route callers
   don't change. Plumbing: the MCP server binds the actor once per
   session/request (contextvar, same pattern as the request-ID logging
   binding) rather than threading it through every service signature.
4. **Structured logging.** The server binds a per-tool-call request ID with
   `structlog`, same as the HTTP middleware, so every workflow line is
   traceable.
5. **Rate limiting** (existing `api/rate_limit.py`) applies when the HTTP
   transport arrives; stdio (single local client) doesn't need it.

## Transport and how Claude Code connects

**stdio, run as a module of the existing backend package.** The server is
`backend/app/mcp/server.py` (`python -m app.mcp.server`), importing
`app/services/` directly and opening its own SQLAlchemy sessions against
`data/app.db` — no HTTP hop through FastAPI, no serialization detour. SQLite
in WAL mode handles the FastAPI process and the MCP server process writing
concurrently; writes are short transactions, same as today.

Claude Code connects via the project-scoped [`.mcp.json`](../.mcp.json) at
the repo root:

```json
{
  "mcpServers": {
    "pcc": {
      "command": "bash",
      "args": ["-c", "cd backend && exec .venv/bin/python -m app.mcp.server"]
    }
  }
}
```

The day slice 3 merges, Claude Code is PCC's first agent client. **Streamable
HTTP is deferred** until a consumer needs it (the in-app agent loop can import
the tool functions in-process; LAN MCP clients would be the trigger). The
tool layer is written transport-agnostic so switching is a server-wiring
change, not a rewrite.

## Dependency to add (signed off 2026-07-10; pinned in `requirements.lock`)

**`mcp` — the official Anthropic Python SDK** (`pip install mcp`), pinned in
`backend/pyproject.toml` and `requirements.lock`. Its FastMCP API gives
decorator-based tool registration with schema generation from type hints, and
it's the reference implementation of the protocol — actively maintained, no
extra transitive weight beyond what the backend already uses (Pydantic v2,
anyio ecosystem). No other new dependency is needed for slice 3.

Rejected: hand-rolling JSON-RPC over stdio (protocol conformance burden for
zero benefit); third-party wrappers around the SDK (unnecessary layer).

## Runtime (decided 2026-07-10 — slice 2 of the runtime epic)

**The model is served by the workspace-level `../llama-swap/` stack — one
llama-swap proxy owning the RTX 3060, with a single shared model entry
(`gemma-4-12b`) that both chess and PCC's agent use.** PCC's provider (slice
3) speaks OpenAI wire format to `http://host.docker.internal:8200/v1`
(`LLAMACPP_BASE_URL`), naming model `gemma-4-12b`.

Why this shape, given both apps converged on the same model (gemma-4-12B
UD-Q4_K_XL, proven native tool calling):

- **llama-swap with a single entry** was chosen over a plain shared
  llama-server compose. The swap machinery is idle today (nothing to evict),
  but the "one owner for the GPU" property is the durable part: the next GPU
  workload (immich ML, voice) becomes a `config.yaml` entry behind the same
  front door instead of a new contention story. The proxy also gives per-model
  `ttl` unload and a `/running` endpoint for free.
- **Rejected: PCC pointing at chess's private `llama` container.** Cheapest,
  but it couples PCC's agent availability to chess's compose lifecycle
  (`docker compose down` in chess kills PCC's agent), leaves the server flags
  owned by a repo that has no reason to know PCC's context needs, and squats
  chess's port block. Chess's container is retired instead (chess PR #87).
- **Rejected: two llama-server instances.** Same weights twice on a 12 GB
  card doesn't fit; pointless when one server serves both.

**Agreed server flags** live in `../llama-swap/config.yaml` — that file is the
single owner; neither app repo carries flags anymore. The tuned chess set
(full GPU offload, MTP speculative decoding, `--jinja` tool calling) plus one
change negotiated for PCC's agent loop: **`-c 131072` with `q8_0` KV cache**
(chess ran 8k/f16) — the model's full 128k window, affordable because gemma's
sliding-window attention keeps the KV small. Measured on the 3060
(2026-07-10): 9.5 GB loaded, 10.5 GB total-GPU peak during a 125k-token
needle test (retrieved correctly); MTP intact. The cost is depth-dependent
speed only: ~112 tok/s generation shallow → ~41 tok/s at full depth, prefill
~446–680 tok/s (a cold 125k prompt is ~5 min — the agent loop grows context
incrementally, so this is a worst-case bound, not a typical call). Sampling
flags are server defaults only; the provider sets its own per request.

Operational notes for slice 3: the proxy binds `127.0.0.1:8200` *and*
`172.17.0.1:8200` (docker0) — consumer containers reach it via
`host.docker.internal` + the `extra_hosts: ["host.docker.internal:host-gateway"]`
stanza. Cold load after the 10-minute `ttl` unload is ~100 s worst case (~5 s
with the GGUF in page cache); the provider needs a first-request timeout that
tolerates it. Decision history: `../future-plans/llama-swap.md`.

## Provider layer (landed 2026-07-11)

`backend/app/ai/providers/llamacpp.py` — PCC's client for the runtime above,
built on `httpx` (no SDK; already a pinned transitive of `mcp`, promoted to a
declared dependency). What it guarantees:

- **OpenAI wire format, validated at the boundary.** Responses parse into
  Pydantic wire models; tool-call `arguments` must be a JSON object and
  structured outputs must satisfy their schema, or a typed error is raised
  (`ProviderRequestError` / `ProviderResponseError` /
  `ToolCallArgumentsError`) — no best-effort parsing. Self-correction retries
  on bad tool calls belong to the agent loop (next checkout); the errors
  carry the tool name for exactly that.
- **`chat(messages, tools=…)`** for tool calling (`tool_choice: auto`), with
  `ChatResult.to_message()` + `tool_result_message()` covering the follow-up
  turn; **`chat_structured(messages, schema=…)`** for grammar-constrained
  output via `response_format: json_schema`, validated into the schema.
- **Gemma quirks handled** (imported from chess's production experience):
  `reasoning_content` is never answer text and never round-trips into
  history; thinking toggles per request via `chat_template_kwargs` and
  defaults off. Sampling (`temp 1.0 / top-p 0.95 / top-k 64`) is set per
  request, so server-default drift can't change PCC's behavior.
- **Config:** `LLAMACPP_BASE_URL` (dev default `http://127.0.0.1:8200/v1`;
  compose overrides to `host.docker.internal`), `LLAMACPP_MODEL`
  (`gemma-4-12b`), `LLAMACPP_TIMEOUT_SECONDS` (300 — tolerates the cold
  load). `provider_from_settings()` builds the configured instance.
- **Structured logs** (`llm_request` / `llm_response` with a per-call
  `llm_call_id`, duration, token usage) join whatever request ID is bound.

Verification until the loop exists: unit tests fake the wire
(`tests/test_ai_llamacpp.py`); the live smoke is opt-in —

```bash
cd backend
PCC_LLM_INTEGRATION=1 .venv/bin/pytest tests/test_ai_llamacpp_integration.py -v
```

which exercises a real tool-call round trip and a structured extraction
against the shared server (passed 2026-07-11, ~6 s warm-cache).

## Tool registry + agent loop core (landed 2026-07-11 — slice 1 of the loop epic)

The MCP server's tool bodies moved verbatim into a transport-agnostic
registry, `app/tools/registry.py` (per-call session/actor/request-ID plumbing
in `app/tools/runtime.py`, absorbing the old `app/mcp/runtime.py`).
`app/mcp/server.py` is now pure wiring: a FastMCP instance that registers
every registry tool. Argument models and JSON Schemas come from the same
`func_metadata` machinery FastMCP uses, so the `ToolSpec`s the registry emits
for the provider are byte-identical to the MCP `inputSchema`s — a parity test
asserts it. `registry.call_tool(name, arguments, actor=…)` is the loop-facing
dispatch: validate against the tool's argument model, stamp the actor, run.

`app/ai/loop.py` is the in-app loop (`AgentLoop.run(user_message)`): a layered
system prompt (see below), at most `max_iterations` (default 10) provider
turns, terminate on a text turn. Self-correction is bounded separately
(default 3): schema-level failures — unparseable tool-call arguments from the
provider, argument-model rejections, unknown tool names — are fed back to the
model and billed against the correction budget, while service-layer domain
rejections ("blocked", "not found", cycles) are ordinary tool-result feedback
under the iteration budget, exactly as the MCP server surfaces them. Writes
are stamped **`agent:loop`** in `activity_events`; the loop binds one request
ID per run (unless the caller already bound one) so every tool call and the
provider's `llm_call_id`-tagged lines correlate. Tests drive the whole thing
with a scripted provider (`tests/test_agent_loop.py`) — no GPU.

## Layered personality (landed 2026-07-11 — Phase 1 of the agents master plan)

The loop's system prompt is composed in layers per the workspace agent
standard (`../agent-standard/STANDARD.md` §5), replacing the earlier single
hardcoded prompt. `build_system_prompt(today)` in `app/ai/loop.py` concatenates:

1. **App base prompt** (`_APP_BASE_PROMPT`, app-owned) — PCC's behavioral
   contract and tool guidance: look things up before writing, prefer the
   specific tool, soft-delete-only, self-correct on rejection, and state only
   what the tools confirmed (never invent an id/task/outcome). These are the
   old prompt's rules, minus the date line.
2. **Global Glitch** — the house personality, vendored verbatim as
   `app/ai/personality-global.md` (canonical in `agent-standard/`). The loader
   strips the one leading `<!-- vendored … -->` header and never edits the
   body; fix drift by re-copying (`../agent-standard/check-sync.sh`). PCC ships
   **no app-flavor layer** — nothing has earned one.
3. **Dynamic layer** — today's date, injected per run.

The vendored `.md` ships in the image for free: the Dockerfile `COPY backend/`
+ editable install (`pip install -e .`) keeps the source tree in place, so the
`Path(__file__)`-relative read resolves at runtime. Composition order and layer
presence are unit-tested (`test_agent_loop.py`); the eval baseline below was
re-run green under the layered prompt (Glitch's brevity did not degrade tool
honesty).

## Conversation persistence + agent API (landed 2026-07-11 — slice 2 of the loop epic)

Two tables (migration `7efad5645027`): soft-deletable `conversations`
(auto-titled from the first user message) and immutable
`conversation_messages`. The assistant turn persists the loop outcome
denormalized — `tool_calls` JSON (the `ToolCallRecord` list: arguments +
result/error per dispatched call) and `stop_reason` — because
`activity_events` records only mutations and carries neither arguments nor
results; the audit log stays the source of truth for what *changed*, the
message row for what the *conversation saw*. Writes go through
`app/services/conversations.py` only.

The API (`app/api/routes_agent.py`, `/api/agent/...`): create/list/fetch/
delete conversations, plus `POST /conversations/{id}/messages` — the one
model-calling endpoint. It commits the user turn *before* running the loop
(the loop's tool calls open their own sessions, so holding the request
transaction would contend on SQLite's write lock; and a provider failure,
surfaced as 502, must not swallow the user's message), runs the loop
synchronously (v1 is non-streaming; the response carries the full tool
trajectory for the panel), then persists the assistant turn. Rate-limited
per client IP via the retained limiter (`agent_messages_per_min`, default
10). Loop context on follow-ups is rebuilt from prior user/assistant *text*
turns only — tool transcripts never round-trip (they'd bloat the 12B's
window; the model re-reads live state through tools).

Verified end-to-end on the live runtime (2026-07-11): a three-step ask
(create project → create high-priority task → complete it) ran in ~9 s warm,
including two genuine self-corrections the loop fed back and the model fixed;
the follow-up message answered from persisted history without tool calls.

## Delegate attribution (landed 2026-07-11 — Phase 1 of the agents master plan)

`POST /conversations/{id}/messages` accepts the `X-Agent-Actor` header per
the workspace delegate contract (`../agent-standard/delegate-api.md`): a
trusted delegate caller — conductor sends `X-Agent-Actor: agent:conductor`
on every call — gets its runs' mutations stamped with its own identity in
`activity_events`, so the audit trail distinguishes conductor-driven writes
from PCC's own chat panel. `resolve_actor` (`app/ai/loop.py`) recognizes
exactly the delegate-actor set (`agent:conductor` today); an absent or
unrecognized value falls back to `agent:loop` — the contract's
ignore-unknown-actors rule, so a caller can never stamp an arbitrary identity
into the audit trail. Missing *and* soft-deleted conversations 404 across
GET / POST-messages / DELETE — the semantics conductor's
recreate-and-retry-once depends on. All covered in `tests/test_agent_api.py`.

## Chat panel (landed 2026-07-11 — slice 3 of the loop epic)

`frontend/src/features/agent/` behind the **Agent** nav entry: conversation
sidebar + thread at `/agent/:id` (reload-safe), the assistant turn rendering
its full persisted tool trajectory — failed self-correction attempts
included — with an undo affordance per successful mutation (create → trash,
trash → restore, complete → reopen; through the same REST endpoints as the
rest of the UI, so undo is audited). v1 is non-streaming (recorded decision):
optimistic bubble + working indicator while the loop runs; SSE only if real
usage makes the wait feel bad. Browser-verified with `verifier-browser`
against the live model.

## Eval harness + gemma-4-12b baseline (landed 2026-07-11 — slice 4 of the loop epic)

`tests/test_agent_evals.py` — scripted scenarios through the full loop +
registry against the **real** runtime; opt-in like the provider smoke:

```bash
cd backend
PCC_AGENT_EVALS=1 .venv/bin/pytest tests/test_agent_evals.py -v -s
```

Each scenario seeds a fresh DB via the service layer and asserts trajectory
*shape* (reads precede writes; read-only asks mutate nothing) plus DB
end-state and audit invariants — never exact call sequences (the model is
sampled at temp 1.0). `-s` prints per-run `[eval]` stats lines.

Re-run green under the layered personality prompt (2026-07-11, 2 consecutive
suites, 12/12 pass): trajectories unchanged in shape, `honest_about_missing`
conceded in 2–3 iterations, and the recurring `create_task` self-correction
still fixes itself — Glitch's brevity contract did not degrade tool honesty.

**Baseline (gemma-4-12b UD-Q4_K_XL, 2026-07-11, 4 consecutive suite runs —
24/24 pass, warm model):**

| Scenario | Asserts | Iterations | Warm time |
| --- | --- | --- | --- |
| `create_task_with_fields` | project routing, priority, "tomorrow" date math, `agent:loop` audit | 3–5 | 3.0–5.9 s |
| `find_and_complete` | retrieval tripwire: described (not named) task found via read, then completed | 3 | 1.2–1.8 s |
| `reschedule` | targeted due-date update; nothing else touched | 3 | 1.5–4.5 s |
| `delete_is_soft` | delete lands in the trash, restorable, audited | 3 | 1.0–3.2 s |
| `read_only_count` | correct count in the reply; zero mutations, zero events | 3 | 1.4–2.0 s |
| `honest_about_missing` | missing target: nothing invented or acted on | 4–10 | 2.2–4.1 s |

Baseline observations worth keeping:

- **Self-correction pays for itself**: in 3 of 4 runs `create_task_with_fields`
  needed 1–2 corrected attempts (recurring gemma miss: `name` instead of
  `title`, numeric priority) and always fixed itself on the validation
  feedback — end state correct every time.
- **FTS5 retrieval is sufficient**: the `search` tool located the described
  task on the first read in every run. Nothing in this baseline justifies
  embeddings or `sqlite-vec` — this table is the tripwire; revisit only if a
  regression here says otherwise.
- `honest_about_missing` over-searches before conceding (search → per-project
  lists → trash, up to ~10 turns); the scenario runs with `max_iterations=14`
  headroom since the honesty asserts, not search frugality, are its point.

## Explicitly deferred (later checkouts)

- **RAG beyond the `search` tool / `sqlite-vec`** — only if the eval baseline
  regresses on retrieval.
- **Streaming (SSE)** — only if real chat-panel usage makes the synchronous
  wait feel bad.
- **RAG / retrieval infra** — the `search` tool *is* the retrieval story for
  now (agentic FTS5 per `TODO.md`); `sqlite-vec` only if that proves
  insufficient.
