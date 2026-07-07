# Project Command Center

A local-first project and task management web app with AI-assisted task capture
from messy text and Discord input. Designed to eventually run on custom
Unsloth-trained models served via llama.cpp.

## Core principle

**The app owns the logic. AI only returns structured suggestions.**

```
Good:  Python workflow → AI extracts tasks → Python validates → Python saves
Bad:   AI decides everything and directly edits the database
```

## Stack

```
Frontend:      React + Vite + TypeScript (lucide-react icons)
Backend:       FastAPI
Database:      SQLite
ORM:           SQLAlchemy 2.0 (typed syntax)
Migrations:    Alembic
Validation:    Pydantic v2
Logging:       structlog (with request IDs)
AI Runtime:    Ollama (v1) → llama.cpp (v2, custom models)
Training:      Unsloth
Discord:       discord.py (separate process, HTTP to the API)
Backups:       scripts/backup_db.sh (stdlib sqlite3 online backup) + cron;
               Litestream continuous WAL replication (docker sidecar)
```

## Architecture

```
React Web App
  ↓
FastAPI Backend
  ├── Project / Task / Inbox / Today / Calendar / Search / Trash / Training APIs
  ├── AI Workflows  ──→  ModelGateway  ──→  Provider (Ollama / llama.cpp)
  ├── Settings/Config API
  └── Discord API endpoints
       ↓
SQLite Database

Discord Bot (separate process)
  ↓ (shared secret auth)
FastAPI Backend
```

**The model gateway is the single most important architectural decision.**
Workflow code never calls Ollama directly — always
`workflow → ModelGateway → provider`. That's what lets the custom-trained
llama.cpp model swap in later without touching workflow code.

## Repo layout

```
backend/app/
  main.py, config.py, logging_config.py
  api/          route modules (routes_*.py), guards, rate limiting
  db/           models.py, session.py
  alembic/      migrations
  services/     one responsibility per module (tasks, inbox, projects,
                trash, today, recurrence, dependencies, training_data, …)
  ai/
    gateway.py, schemas.py, profiles.yaml
    providers/  base.py, ollama.py  (llamacpp.py planned, not built)
    prompts/    *.md — one per workflow, editable at runtime via Settings
    workflows/  extract_tasks, match_project, summarize_project, break_down_task
    evals/      *_cases.yaml + run_*_evals.py per workflow
  integrations/discord/   bot.py, commands.py

frontend/src/
  api/          all HTTP calls (components consume hooks; hooks call this layer)
  features/     feature folders: dashboard, projects, tasks, inbox, today,
                calendar, search, settings, training, trash, errors
  components/   shared primitives (Button, Card, Badge, Modal, AppShell, …)
  routes/, types/

training/       exports + Unsloth fine-tune scripts (future)
data/           app.db + backups/
scripts/        backup_db.sh
main.sh         dev bootstrap + run
test.sh         full quality gate
```

## Database schema

Tables: `projects`, `project_aliases`, `tasks`, `task_dependencies`,
`inbox_items`, `activity_events`, `eval_runs`, `ai_training_examples`.

Key decisions:

- **Soft deletes everywhere** via `deleted_at`; queries filter it by default
  through a service-layer helper. The only true delete is user-triggered purge
  from `/trash`, and only of already-soft-deleted rows. Exceptions:
  `activity_events` and `eval_runs` are append-only logs with no `deleted_at`.
- **Candidates and real tasks share the `tasks` table**, split by
  `review_status` (`candidate | accepted | rejected`). User-facing progress is
  separate: `workflow_status` (`open | in_progress | done`).
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
- `inbox_items.input_hash` gives idempotency — the same input text never
  re-extracts. `inbox_items.matched_alias` records which project alias routed a
  note (when the deterministic matcher matched an alias, not the project name),
  surfaced at triage as "matched alias '…'".
- **`tasks` read-path indexes** back the hot list/read queries: a compound
  `(deleted_at, review_status)` (active-task list, calendar, search, and — via
  its leading column — the trash `deleted_at IS NOT NULL` scan) plus single
  `project_id`, `parent_task_id`, `recurrence_id`. `workflow_status` is
  deliberately not indexed (effective status rolls up in Python; it is never a
  SQL filter).

### The most important table

```
ai_training_examples
- task_name           ("extract_tasks", "match_project", …)
- input_text          (raw input, exactly as the model saw it)
- model_output_json   (FULL model output, not just the diff)
- corrected_output_json
- accepted (bool), model_profile, model_name, created_at
```

Every AI correction the user makes lands here automatically — this is the
fine-tuning corpus. Never store just the diff; the full input/output pair is
what training needs.

## AI subsystem

Profiles live in `backend/app/ai/profiles.yaml` (currently four:
`task_extraction`, `break_down_task`, `project_matching`, `summary` — all on
Ollama `gemma4:e2b`, JSON-schema mode except the text-mode summary). Local
tuning via the Settings UI writes to `profiles.local.yaml` (gitignored),
deep-merged over the committed defaults.

The inbox capture flow:

```
Raw inbox text
→ hash input, dedupe (idempotency) → save inbox item
→ extraction model via gateway → Pydantic validation
→ task rows with review_status="candidate"
→ user reviews inline → accept/edit/reject
→ correction saved to ai_training_examples
```

Every workflow has prompt file(s) in `ai/prompts/*.md` and eval cases in
`ai/evals/*_cases.yaml`. The Settings page edits profiles and prompts at
runtime, runs evals per suite with pass-rate history, and shows Ollama health.

## Status & roadmap

Sprints 0–25 shipped: full task/project/inbox core, Discord bot, recurrence,
subtasks + dependencies, Today page, trash, training-data meter, Settings, and
a UI/UX in-place-editing revamp. A Gantt/calendar planning epic (Sprints 17–24)
was built and then **removed** — it didn't earn its complexity.

- `CURRENT.md` — the checked-out focus (currently: none)
- `TODO.md` — the backlog, theme-grouped
- `DONE.md` — sprint-by-sprint changelog

Still on the roadmap:

```
Sprint 10: Export ai_training_examples → Unsloth fine-tune → llama.cpp swap
           (gated on 200+ training examples — the /training meter tracks this)
```

## Do not build yet

```
Custom models           (wait for ~200+ real training examples)
Discord buttons · Calendar sync · Obsidian integration · Email ingestion
Multi-user auth · Celery / Redis · Vector DB · Autonomous agents
```

## Dev commands

```
./main.sh                 # bootstrap env/deps, migrate, start Ollama + backend
                          # + frontend (+ Discord bot when tokens are set)
./test.sh                 # backend pytest/ruff/mypy + frontend Vitest/lint/build
./test.sh --ai-evals      # also run the Ollama-backed AI eval suites (opt-in:
                          # they need Ollama + the configured local model)
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
the built SPA and reverse-proxying `/api` to the backend). **Ollama is not
containerized** — it stays on the host (GPU) and the backend reaches it over the
host gateway.

Prerequisites: Docker + the compose plugin, and Ollama already running on the
host with the configured model pulled.

```
cp .env.example .env          # then edit: secrets, exposure, trusted proxy
docker compose up --build     # backend + frontend
```

The dashboard is **host-only by default** (`http://127.0.0.1:8100`), matching the
`API_HOST`/`DEV_HOST` posture. To expose it on the LAN, set `FRONTEND_BIND=0.0.0.0`
in `.env` (optionally `FRONTEND_PORT`; 8100 is PCC's slot in the workspace port
registry — see `../gateway/README.md`). The workspace gateway proxies
`tasks.$HOMELAB_DOMAIN` here and expects the LAN-exposed setting, so the
Settings-write re-guard applies to proxied clients. The backend itself publishes
no host port; it is reachable only via nginx and the compose network.

**Discord bot** (optional) runs as a compose profile so it only starts when asked:

```
docker compose --profile discord up   # needs DISCORD_BOT_TOKEN + BACKEND_SHARED_SECRET in .env
```

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

**Settings writes in docker mode.** Profile/prompt saves and eval runs (the
loopback-guarded routes) **work from the host by default.** The reasoning: with
the dashboard bound host-only (the default), the LAN cannot reach nginx at all,
so every request nginx forwards is necessarily from the host — the backend trusts
those. The moment you expose the dashboard on the LAN (`FRONTEND_BIND=0.0.0.0`),
the backend **automatically re-guards** these writes to `403` for proxied clients;
no second switch to remember. This is driven by two values the compose file passes
to the backend: `TRUSTED_PROXY_IPS` (the nginx/compose subnet) and `FRONTEND_BIND`.

The guard never trusts `X-Forwarded-For` to *look* like loopback (the leftmost
entries are client-forgeable), so a spoofed header can't smuggle a write past it.
For rate limiting behind nginx, the backend keys on the address nginx actually
observed (the rightmost `X-Forwarded-For` entry), which a client can't fake.

In LAN-exposed mode, make Settings changes from the `./main.sh` dev environment or
from inside the backend container — a request from within the container is a true
loopback client (the slim image has Python, not curl):

```
docker compose exec backend python -c \
  "import urllib.request,json; \
   req=urllib.request.Request('http://127.0.0.1:8000/api/settings/profiles/task_extraction', \
   data=json.dumps({'temperature':0.5}).encode(), method='PATCH', \
   headers={'Content-Type':'application/json'}); \
   print(urllib.request.urlopen(req).read().decode())"
```

## Network & security posture

Single-user, trusted-LAN app. `API_HOST` and `DEV_HOST` default to `127.0.0.1`;
setting `API_HOST=0.0.0.0` is intentional and supported:

- Normal project/task/inbox/trash/training routes are open to LAN reads and writes.
- **Settings writes stay host-only** (profile/prompt saves, eval runs) — LAN
  clients get `403`. Read-only Settings routes (Ollama health, installed models)
  work from the LAN. On a direct bind the guard trusts loopback peers. Behind the
  docker reverse proxy it trusts writes the proxy forwards *only while the
  dashboard is bound host-only* (`FRONTEND_BIND`); exposing it on the LAN
  re-guards them automatically. `X-Forwarded-For` is never trusted to look like
  loopback, so a spoofed header can't bypass the guard.
- Discord routes are protected by `BACKEND_SHARED_SECRET`, not the bind address.
- The two Ollama-calling routes (`POST /api/discord/inbox`,
  `GET /api/projects/{id}/summary`) are per-IP rate limited
  (`RATE_LIMIT_DISCORD_INBOX_PER_MIN`, `RATE_LIMIT_SUMMARY_PER_MIN`).

This is not multi-user auth; revisit real auth before exposing beyond a home LAN.

## Discord setup

1. Create the app + bot at https://discord.com/developers/applications; copy the
   bot token.
2. In `backend/.env` set `DISCORD_BOT_TOKEN` and `BACKEND_SHARED_SECRET` (any
   long random string; empty disables the discord route with 503). Optional
   `DISCORD_GUILD_ID` for instant slash-command registration while testing.
3. Invite via OAuth2 URL Generator: scopes `bot` + `applications.commands`,
   permission `Send Messages`.
4. Run `./main.sh`. Commands available in Discord:
   - `/inbox <messy text>` → bot replies with extracted titles; candidates land
     in the web app's inbox for review.
   - `/tasks [project]` → numbered list of open tasks (accepted, not done),
     optionally filtered to a project by name or alias; long lists are capped
     with an "…and N more" line.
   - `/done <search>` → fuzzy title search over open tasks. One match completes
     it (recurring tasks spawn their next occurrence, same as the web app);
     several matches list the candidates so you can narrow the search — it never
     completes a task on an ambiguous match; no match says so.

## North star

A **boring, reliable local app** where AI is a helper, not the boss: React UI +
FastAPI core + SQLite truth + small local model calls through a gateway +
Pydantic validation + review queue + training-data collection + eventual custom
llama.cpp models trained on your own corrections.
