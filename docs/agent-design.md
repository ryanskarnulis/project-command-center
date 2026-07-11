# Agent design — PCC MCP server (Phase 2)

Design for the first Phase 2 building block: an MCP server that exposes the
service layer as tools, making any MCP client (Claude Code first, the in-app
llama.cpp agent later) a full peer of the UI. This doc scopes slice 3 of the
Phase 2 kickoff epic (`CURRENT.md`); the llama.cpp runtime, agent loop, chat
UI, and RAG are explicitly deferred (see the end).

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
| `trash_task` | `tasks.soft_delete_task` | The only delete the agent gets. |

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
| `restore_task` / `restore_project` | `task_trash.restore_task` / `projects.restore_project` | Undo path for every agent delete. |
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

Claude Code connects via a project-scoped `.mcp.json` at the repo root:

```json
{
  "mcpServers": {
    "pcc": {
      "command": "backend/.venv/bin/python",
      "args": ["-m", "app.mcp.server"],
      "cwd": "backend"
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
change negotiated for PCC's agent loop: **`-c 16384` with `q8_0` KV cache**
(chess ran 8k/f16). Measured on the 3060: 7.9 GB VRAM (down from 10.2 GB —
the KV quant more than pays for the doubled context), MTP intact (~112 tok/s,
119/124 draft acceptance on a tool call). Sampling flags are server defaults
only; the provider sets its own per request.

Operational notes for slice 3: the proxy binds `127.0.0.1:8200` *and*
`172.17.0.1:8200` (docker0) — consumer containers reach it via
`host.docker.internal` + the `extra_hosts: ["host.docker.internal:host-gateway"]`
stanza. Cold load after the 10-minute `ttl` unload is ~100 s worst case (~5 s
with the GGUF in page cache); the provider needs a first-request timeout that
tolerates it. Decision history: `../future-plans/llama-swap.md`.

## Explicitly deferred (later Phase 2 checkouts)

- **Provider layer** (`ai/providers/llamacpp.py`) — slice 3 of the current
  epic, against the runtime above.
- **Agent loop + conversation persistence** — consumes this tool surface
  later; nothing here blocks on it.
- **Chat panel UI** — needs the loop first.
- **RAG / retrieval infra** — the `search` tool *is* the retrieval story for
  now (agentic FTS5 per `TODO.md`); `sqlite-vec` only if that proves
  insufficient.
