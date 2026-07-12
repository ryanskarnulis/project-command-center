# Project Command Center

A local-first project and task management web app: projects, tasks (subtasks,
dependencies, recurrence), Focus, search, trash, dashboard — plus a local
agent (llama.cpp + tools + MCP + retrieval) that operates the app through its
service layer.

## Core principle

**The app owns the logic. The service layer is the only write path.**

The UI, the API, and the agent are all peers that mutate state through
the same service layer — validation, soft deletes, activity events, and rollups
apply identically no matter who is calling. An agent tool that bypasses the
service layer is the same bug as a route handler that writes raw SQL.

## Stack

```
Frontend:      React + Vite + TypeScript (lucide-react icons)
Backend:       FastAPI
Database:      SQLite
ORM:           SQLAlchemy 2.0 (typed syntax)
Migrations:    Alembic
Validation:    Pydantic v2
Logging:       structlog (with request IDs)
Backups:       scripts/backup_db.sh (stdlib sqlite3 online backup) + cron;
               Litestream continuous WAL replication (docker sidecar)
```

## Repo layout

```
backend/app/
  main.py, config.py, logging_config.py
  api/          route modules (routes_*.py), guards, rate limiting
  db/           models.py, session.py
  schemas/      Pydantic v2 request/response schemas, one module per feature
  alembic/      migrations
  services/     one responsibility per module (tasks, projects, trash, focus,
                recurrence, dependencies, …)
  tools/        transport-agnostic agent tool registry + per-call runtime
  mcp/          PCC MCP server (stdio): the tool registry over MCP
  ai/           llama.cpp provider layer (llama-swap runtime) + agent loop

frontend/src/
  api/          all HTTP calls (components consume hooks; hooks call this layer)
  features/     feature folders: dashboard, projects, tasks, focus, search,
                trash, agent, errors
  components/   shared primitives (Button, Card, Badge, Modal, AppShell, …)
  routes/, types/

data/           app.db + backups/
scripts/        backup_db.sh, refresh_design_kit.sh
main.sh         dev bootstrap + run
test.sh         full quality gate
```

## Database schema

Tables: `projects`, `tasks`, `task_dependencies`, `activity_events`,
`conversations`, `conversation_messages`.

Key decisions:

- **Soft deletes everywhere** via `deleted_at`; queries filter it by default
  through a service-layer helper. The only true delete is user-triggered purge
  from `/trash`, and only of already-soft-deleted rows. Exception:
  `activity_events` is an append-only log with no `deleted_at`. Its nullable
  `actor` column attributes each event: `NULL` is the user; agents stamp an
  identifier (the MCP server writes `agent:mcp`, the in-app agent loop
  `agent:loop`, a conductor-delegated run `agent:conductor`).
- Task progress lives in `workflow_status` (`open | in_progress | done`). The
  AI-era `review_status`/`confidence`/`assignee_hint` columns are dropped;
  every task is user-facing and always filed in a project (no project on
  create/update means General).
- **Subtasks** nest via nullable `parent_task_id` (a tree — cycles refused with
  `409`). Deleting a parent cascade-soft-deletes the subtree; restore is
  per-task. A parent's estimate and status **roll up from its subtasks**
  (derived in `services/tasks.compute_rollups`, never stored); direct status
  writes on such a parent return `409`.
- **Dependencies** are `A depends_on B` edges in `task_dependencies` (B must be
  done before A starts). "Blocked" is never stored — `is_blocked`,
  `is_blocking`, and `blocked_task_count` are derived in Python from the active
  edge graph. The service layer refuses cycle-creating edges.
- **Recurrence** via a shared `recurrence_id` string across a series;
  scheduling math lives in `services/task_recurrence.py`.
- **Project deletion cascade**: deleting a project soft-deletes its tasks,
  stamped with `deleted_with_project_id` so restore can offer to bring them
  back together. A protected `General` project is seeded (system key
  `general`).
- **Agent conversations** persist as `conversations` (soft-deletable,
  auto-titled from the first user message) and immutable
  `conversation_messages`. The assistant turn stores the loop's tool
  calls/results as JSON plus a `stop_reason` — the chat trajectory lives
  here (the audit log records only mutations and can't reconstruct it), while
  `activity_events` remains the audit source of truth for what changed.
- **`tasks` read-path indexes** back the hot list/read queries: `deleted_at`
  (active-task list, search, and the trash `deleted_at IS NOT NULL` scan) plus
  single `project_id`, `parent_task_id`, `recurrence_id`. `workflow_status` is
  deliberately not indexed (effective status rolls up in Python; it is never a
  SQL filter).

## Dashboard workflow

The dashboard is a project-swimlane board built for moving work. Each active
project has an Open / In progress lane with its open count and derived status
tone; projects with no active work stay collapsed until needed. Task cards use
the same dependency guards and recurrence-safe done/reopen paths as the project
boards. Completed tasks are fetched lazily behind each lane's **Show done**
toggle instead of occupying a permanent column.

A slim signal strip above the lanes counts overdue, blocking, and due-today
root tasks. Selecting a signal filters every project lane; selecting it again
returns to the full board.

## MCP server (agent access)

The service layer is exposed as ~25 agent tools (task CRUD + complete,
project CRUD, search, focus plan, trash/restore, activity log, dependencies,
recurrence skip/stop). The tools live in a transport-agnostic registry
(`app/tools/registry.py`) consumed by two peers: the in-app agent loop
(below) and a
stdio server: `python -m app.mcp.server`, run from `backend/`. Design and
guardrails: [`docs/agent-design.md`](docs/agent-design.md). In short: writes
go through the same service layer as the UI, arguments are Pydantic-validated
at the boundary, every mutation lands in `activity_events` as `agent:mcp`,
and no hard-delete tool exists — agent deletes are always restorable from the
trash.

The repo ships a project-scoped [`.mcp.json`](.mcp.json), so Claude Code
started in this directory picks the `pcc` server up automatically (after the
backend venv exists — run `./main.sh` once first). Any other MCP client
connects with the same command:

```json
{
  "mcpServers": {
    "pcc": { "command": "bash", "args": ["-c", "cd backend && exec .venv/bin/python -m app.mcp.server"] }
  }
}
```

The server opens `data/app.db` directly (WAL mode), so it can run alongside
the dev or docker backend.

## Model runtime & provider (agent)

The model (gemma-4-12b on llama.cpp) is served by the workspace-level
`../llama-swap/` stack on port 8200 — one proxy owning the GPU, shared with
the chess app; server flags live in that repo's `config.yaml`, not here. PCC's
side is `app/ai/providers/llamacpp.py`: OpenAI wire format over `httpx`, tool
calling plus `json_schema` structured outputs, every response
Pydantic-validated at the boundary. Configure with `LLAMACPP_BASE_URL` /
`LLAMACPP_MODEL` / `LLAMACPP_TIMEOUT_SECONDS` (defaults in `app/config.py`;
the docker deployment reaches the host proxy via `host.docker.internal`).
On top of the provider sits the agent loop (`app/ai/loop.py`): a bounded
plan → tool-call → observe cycle over the shared tool registry, with Pydantic
argument validation on every dispatch and bounded self-correction turns when
the model emits an invalid call. Its writes are stamped `agent:loop` in
`activity_events` and every delete is a restorable soft delete.

The loop is driven over REST (`app/api/routes_agent.py`): create/list/fetch/
delete conversations under `/api/agent/conversations`, and
`POST /api/agent/conversations/{id}/messages` — the one model-calling
endpoint — which stores the user turn, runs the loop synchronously, and
returns the exchange with the full tool-call trajectory. It is rate-limited
per client IP (`AGENT_MESSAGES_PER_MIN`, default 10).

The chat panel (`frontend/src/features/agent/`, the **Agent** nav entry)
drives that API: conversations in a sidebar, the thread with every tool call
the agent made rendered on the assistant turn — failed calls included — and
an undo affordance on each mutation (create → trash, trash → restore,
complete → reopen), routed through the same REST endpoints as the rest of
the UI so undo is audited too. v1 is non-streaming: a working indicator
shows while the loop runs.

Two opt-in live suites (the default test run never touches the GPU): the
provider smoke, and the agent eval harness — six scripted scenarios through
the real loop asserting trajectories and DB end-state, with the gemma-4-12b
baseline recorded in [`docs/agent-design.md`](docs/agent-design.md):

```bash
cd backend && PCC_LLM_INTEGRATION=1 .venv/bin/pytest tests/test_ai_llamacpp_integration.py -v
cd backend && PCC_AGENT_EVALS=1 .venv/bin/pytest tests/test_agent_evals.py -v -s
```

## Status & roadmap

The core is complete and stable: tasks/projects, recurrence, subtasks +
dependencies, Focus, search, trash, dashboard, docker + litestream deploy.

Phase 2 (the local agent) is shipped end-to-end: MCP server, shared
llama.cpp runtime + provider layer, agent loop, chat panel, eval harness,
and the fleet agent-standard alignment (layered personality, `app.yaml`
agent block, delegate attribution). What's next lives in the planning files:

- `CURRENT.md` — the checked-out focus
- `TODO.md` — the backlog
- `DONE.md` — changelog
- [`docs/agent-design.md`](docs/agent-design.md) — the agent design record
  (tool surface, guardrails, runtime, loop, personality, evals)

## Do not build yet

```
Multi-user auth · internet exposure   (trusted home LAN only)
Celery / Redis                        (SQLite + in-process is fine)
External vector DB                    (if embeddings: sqlite-vec, in-process)
Cloud model providers                 (the agent runs locally on llama.cpp)
Obsidian integration · Email ingestion
```

## Dev commands

```
./main.sh                 # bootstrap env/deps, migrate, start backend + frontend
./test.sh                 # backend pytest/ruff/mypy + frontend Vitest/lint/build
./scripts/backup_db.sh    # online snapshot of data/app.db → data/backups/
                          # (prunes past BACKUP_RETENTION_DAYS, default 14)
```

`main.sh` creates missing `.env` files from the examples, builds
`backend/.venv` when needed, installs pinned deps
(`pip install -e '.[dev]' -c requirements.lock`; frontend uses `npm ci`), runs
migrations, and keeps everything in the foreground until Ctrl-C. After
intentionally bumping a backend dependency, regenerate the lock:

```
cd backend && .venv/bin/python -m pip freeze --exclude-editable > requirements.lock
```

## Deploy with Docker

`./main.sh` remains the dev path. For a persistent deployment on a home server,
`docker compose` stands up the backend (uvicorn) and the frontend (nginx serving
the built SPA and reverse-proxying `/api` to the backend).

Prerequisites: Docker + the compose plugin.

```
cp .env.example .env          # then edit: exposure, trusted proxy
docker compose up --build     # backend + frontend
```

The dashboard is **host-only by default** (`http://127.0.0.1:8100`), matching the
`API_HOST`/`DEV_HOST` posture. To expose it on the LAN, set `FRONTEND_BIND=0.0.0.0`
in `.env` (optionally `FRONTEND_PORT`; 8100 is PCC's slot in the workspace port
registry — see `../gateway/README.md`). The workspace gateway proxies
`tasks.$HOMELAB_DOMAIN` here. The backend itself publishes no host port; it is
reachable only via nginx and the compose network.

**Data & backups.** SQLite lives on the bind-mounted `./data` volume
(`app.db` + WAL sidecars survive restarts). `./scripts/backup_db.sh` is still the
snapshot path — run it on the host against `data/app.db`, or from inside the
container.

**Continuous replication (Litestream).** The `litestream` compose service streams
`data/app.db`'s WAL to a replica as writes land, giving point-in-time recovery
between the coarse snapshots `backup_db.sh` takes — the two complement each
other; keep running both. Replica targets, restore procedure, the verified
restore drill, and the non-docker systemd setup are in
[`docs/backups.md`](docs/backups.md).

## Network & security posture

Single-user, trusted-LAN app. `API_HOST` and `DEV_HOST` default to `127.0.0.1`;
setting `API_HOST=0.0.0.0` is intentional and supported:

- Normal project/task/trash routes are open to LAN reads and writes.
- The agent messages endpoint (`POST /api/agent/conversations/{id}/messages`)
  is rate-limited per client IP (`AGENT_MESSAGES_PER_MIN`, default 10); the
  other routes are unlimited on the trusted LAN.

This is not multi-user auth; revisit real auth before exposing beyond a home LAN.
