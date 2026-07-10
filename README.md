# Project Command Center

A local-first project and task management web app: projects, tasks (subtasks,
dependencies, recurrence), Today, search, trash, dashboard. A local agent
(llama.cpp + tools + MCP + retrieval) that operates the app through its service
layer is the next major direction.

> **Direction change (2026-07-09), strip in progress.** The AI-assisted-capture
> / training-data / custom-model track, the inbox, the Discord bot, and the
> calendar have been **removed** — see the strip epic in `CURRENT.md`. The agent
> plan lives in `TODO.md` ("Phase 2 — local agent").

## Core principle

**The app owns the logic. The service layer is the only write path.**

The UI, the API, and the future agent are all peers that mutate state through
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

## Architecture

```
React Web App
  ↓
FastAPI Backend
  └── Project / Task / Today / Search / Trash APIs
       ↓
SQLite Database
```

## Repo layout

```
backend/app/
  main.py, config.py, logging_config.py
  api/          route modules (routes_*.py), guards, rate limiting
  db/           models.py, session.py
  alembic/      migrations
  services/     one responsibility per module (tasks, projects, trash, today,
                recurrence, dependencies, …)

frontend/src/
  api/          all HTTP calls (components consume hooks; hooks call this layer)
  features/     feature folders: dashboard, projects, tasks, today, search,
                trash, errors
  components/   shared primitives (Button, Card, Badge, Modal, AppShell, …)
  routes/, types/

data/           app.db + backups/
scripts/        backup_db.sh
main.sh         dev bootstrap + run
test.sh         full quality gate
```

## Database schema

Tables: `projects`, `project_aliases`, `tasks`, `task_dependencies`,
`activity_events`.

Key decisions:

- **Soft deletes everywhere** via `deleted_at`; queries filter it by default
  through a service-layer helper. The only true delete is user-triggered purge
  from `/trash`, and only of already-soft-deleted rows. Exception:
  `activity_events` is an append-only log with no `deleted_at`.
- Tasks carry a `review_status` (`candidate | accepted | rejected`) alongside
  user-facing progress in `workflow_status` (`open | in_progress | done`); a
  follow-up will collapse `review_status` now that the capture flow that
  produced candidates is gone.
- **Subtasks** nest via nullable `parent_task_id` (a tree — cycles refused with
  `409`). Deleting a parent cascade-soft-deletes the subtree; restore is
  per-task. A parent's estimate and status **roll up from accepted subtasks**
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
- **`tasks` read-path indexes** back the hot list/read queries: a compound
  `(deleted_at, review_status)` (active-task list, search, and — via
  its leading column — the trash `deleted_at IS NOT NULL` scan) plus single
  `project_id`, `parent_task_id`, `recurrence_id`. `workflow_status` is
  deliberately not indexed (effective status rolls up in Python; it is never a
  SQL filter).

## Status & roadmap

Sprints 0–25 shipped the full core (tasks/projects, recurrence, subtasks +
dependencies, Today, trash, dashboard, docker + litestream deploy). A
Gantt/calendar planning epic was built and then **removed** — it didn't earn
its complexity, and the rest of the calendar has followed it out in the strip.
The AI-assisted capture flow, the inbox, the training pipeline, and the Discord
bot have been removed as part of the current strip.

- `CURRENT.md` — the checked-out focus (currently: the strip epic)
- `TODO.md` — the backlog, including the Phase 2 agent plan
- `DONE.md` — changelog

Roadmap in two phases:

```
Phase 1 (in progress): strip AI, training, inbox, Discord, calendar → simple core
Phase 2:        local agent — llama.cpp runtime, PCC MCP server (service layer
                as tools), agent loop, FTS5-first retrieval, chat UI
```

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
`data/app.db`'s write-ahead log to a replica as writes land, giving point-in-time
recovery *between* the coarse snapshots `backup_db.sh` takes. The two **complement
each other — keep running both**; Litestream is not a snapshot archive. By default
it writes a local file replica to `data/replica/` (zero config, no credentials).
That protects against app-level corruption, a bad migration, or a mistaken delete,
but **not disk loss** (the replica shares the mount). For off-host durability,
repoint the replica `path` in `litestream.yml` at an NFS / second-disk mount, or
uncomment the S3 block there and set `LITESTREAM_S3_*` in `.env` — no cloud
dependency is pulled in by default.

Restore runs against the same config. It reconstructs the DB from the replica's
snapshot + WAL into a scratch file you can inspect before going live:

```
docker compose run --rm --no-deps litestream \
  restore -config /etc/litestream.yml -o /data/restored.db /data/app.db
# Go live: stop the app, replace data/app.db with the restored copy, restart.
```

*Restore drill verified 2026-07-03:* with the stack up, a project created through
the API **after** the initial snapshot was present in a `litestream restore` of the
file replica, and every table's row count matched the live DB — confirming the WAL
stream (not just the startup snapshot) round-trips.

**Non-docker (`main.sh`) setup.** Litestream also runs as a plain host binary
against the same `litestream.yml` (point `path` at your real `data/app.db`). Run it
under systemd so it restarts with the box:

```
# /etc/systemd/system/litestream.service — then: systemctl enable --now litestream
[Service]
ExecStart=/usr/local/bin/litestream replicate -config /path/to/litestream.yml
Restart=always
```

## Network & security posture

Single-user, trusted-LAN app. `API_HOST` and `DEV_HOST` default to `127.0.0.1`;
setting `API_HOST=0.0.0.0` is intentional and supported:

- Normal project/task/trash routes are open to LAN reads and writes.
- The rate-limit module is retained in code for future agent endpoints, but
  there are no rate-limited routes right now.

This is not multi-user auth; revisit real auth before exposing beyond a home LAN.

## North star

A **boring, reliable local project manager with a capable local agent**: React
UI + FastAPI core + SQLite truth, and an agent (llama.cpp, tool calling, MCP,
local retrieval) that is a peer of the UI — every agent action goes through the
service layer, is validated, logged to `activity_events`, and undoable via the
trash. No cloud dependencies, no training pipeline, no review queue: undo is
the safety net.
