# Project Command Center

A local-first project and task management web app with AI-assisted task capture from messy text and Discord input. Designed to eventually run on custom Unsloth-trained models served via llama.cpp.

## Core principle

**The app owns the logic. AI only returns structured suggestions.**

```
Good:  Python workflow → AI extracts tasks → Python validates → Python saves
Bad:   AI decides everything and directly edits the database
```

## Stack

```
Frontend:      React + Vite + TypeScript
Icons:         lucide-react
Backend:       FastAPI
Database:      SQLite
ORM:           SQLAlchemy 2.0 (typed syntax)
Migrations:    Alembic
Validation:    Pydantic v2
Logging:       structlog (with request IDs)
AI Runtime:    Ollama (v1) → llama.cpp (v2)
Training:      Unsloth
Discord:       discord.py
Backups:       litestream or cron'd sqlite3 .backup
```

## Architecture

```
React Web App
  ↓
FastAPI Backend
  ├── Project APIs
  ├── Task APIs
  ├── Inbox APIs
  ├── AI Workflows  ──→  ModelGateway  ──→  Provider (Ollama / llama.cpp)
  ├── Settings/Config API
  └── Discord API endpoints
       ↓
SQLite Database

Discord Bot (separate process)
  ↓ (local-only binding + shared secret)
FastAPI Backend
```

## MVP goal

```
Paste messy text into web app
→ AI extracts task candidates
→ user reviews / edits / accepts
→ tasks created in the right project
→ corrections saved as training data
```

Then add Discord:

```
Discord /inbox "finish firewall cleanup by Friday"
→ backend processes it
→ AI extracts task candidates
→ app stores them
→ bot replies with summary
```

## Repo layout

```
project-command-center/
  backend/
    app/
      main.py
      config.py
      logging_config.py

      api/
        routes_projects.py
        routes_tasks.py
        routes_inbox.py
        routes_ai.py
        routes_settings.py
        routes_discord.py

      db/
        models.py
        session.py
      alembic/
        versions/
        env.py
      alembic.ini

      services/
        projects.py
        tasks.py
        inbox.py
        activity.py
        training_data.py

      ai/
        gateway.py
        profiles.yaml
        schemas.py

        providers/
          base.py
          openai_compatible.py
          ollama.py
          llamacpp.py

        prompts/
          extract_tasks.md
          match_project.md
          summarize_project.md

        workflows/
          extract_tasks.py
          match_project.py
          summarize_project.py

        evals/
          extraction_cases.yaml
          run_evals.py

      integrations/
        discord/
          bot.py
          commands.py

  frontend/
    src/
      api/
      features/
        dashboard/
        projects/
        tasks/
        inbox/
        settings/
        search/         (CommandSearch + useSearch + parseCommand — the topbar
                         global search and "/new" / "/done" slash commands)
      components/        (shared primitives: Button, Card, Badge, AsyncState,
                         ToastProvider/useToast, AppShell, Modal)
      routes/
      types/

  training/
    exports/
    unsloth/
      train_task_extractor.py
      datasets/
      models/

  data/
    app.db
    backups/

  docker-compose.yml
  CLAUDE.md
  README.md
```

## Database schema

Tables:

```
projects
project_aliases        (normalized_alias dedupe key + partial unique index over
                        active rows: one alias per project per normalized form)
tasks                  (includes review_status: candidate | accepted | rejected;
                        workflow_status: open | in_progress | done;
                        nullable parent_task_id self-FK for subtask nesting;
                        nullable estimated_minutes effort estimate;
                        nullable breakdown_output_json holding the "break this down"
                        model output between generating and reviewing subtasks;
                        nullable deleted_with_project_id marking tasks cascade-deleted
                        with their project, so restore can bring them back together)
task_dependencies      ("A depends_on B" edges; B must be done before A starts)
inbox_items            (includes input_hash for idempotency)
activity_events
eval_runs
ai_training_examples
```

All tables use **soft deletes** via a `deleted_at` column. Don't actually delete rows — you'll change your mind, and training data references them. The one true delete is **purge from trash** (Sprint 9f/9i): a row must already be soft-deleted, and only the user, from the `/trash` page, triggers it. `ai_training_examples` can be pruned this way too (Sprint 9i, user-approved), but only one row at a time via trash — the active corpus is never bulk-deleted.

> **Exception:** `activity_events` (Sprint 6) is an append-only audit log and has
> **no** `deleted_at` — an audit trail is never user-edited. It records
> project/task lifecycle changes (created/updated/completed/deleted) from the
> service layer and feeds the per-project ActivityFeed.
>
> **Exception:** `eval_runs` (Sprint 7) is the same kind of append-only run log
> (one row per eval-suite run: `suite`, `passed`, `total`) and likewise has **no**
> `deleted_at`. It lets prompt/profile edits be judged as helping or regressing over
> time; surfaced as run history on the Settings page.

Tasks use `review_status` rather than a separate `task_candidates` table. Candidates and real tasks live in the same table, distinguished by review lifecycle. User-facing progress lives in `workflow_status` (`open`, `in_progress`, `done`) so training/review state does not leak into normal task management.

A protected `General` project is seeded with the stable system key `general`.
Deleting any other project **cascade-soft-deletes its tasks (and their subtrees)
along with it** — each task is stamped with `tasks.deleted_with_project_id` so the
set can be brought back together. Restoring the project asks whether to bring those
tasks back: confirm and the project and its tasks return together; decline and only
the project shell is restored (the tasks stay in `/trash`). Tasks the user trashed
independently before the project delete keep a null marker and are never swept back.
Cascade-deleted tasks don't appear as standalone rows in the Tasks section of
`/trash`; they restore with their project. (This replaces the earlier
rehome-to-`General`-on-delete behavior.)

Tasks nest via a nullable self-referential `parent_task_id` (a tree, not a graph:
a self-/ancestor-cycle is refused with a `409`, guarded in `services/tasks.py`).
Soft-deleting a parent **cascade-soft-deletes its whole subtree**; restore is
per-task (restoring a parent does not auto-restore children — each is restorable
from `/trash`). Ordering tasks is separate from nesting: `task_dependencies` holds
`A depends_on B` edges meaning **B must be workflow-`done` before A can start**.
"Blocked" is never a stored status — it's derived in Python from the active edges
and the depended-on tasks' workflow status (`TaskRead.is_blocked`, resolved in
one bulk query). Sprint 16 also derives root-cause blocking signals
(`TaskRead.is_blocking` + `blocked_task_count`) from the same active dependency
graph: only the highest unfinished accepted blocker in a chain gets the red
blocking marker, and the count is transitive downstream work waiting on it. The
same `services/task_dependencies.py` cycle guard refuses any edge that would
create an `A→B→A` deadlock (prime directive #1: the app owns the logic).

A parent's **estimate and progress are likewise derived, not stored**
(`services/tasks.compute_rollups`, folded into `TaskRead` the same way as
`is_blocked`): with accepted subtasks present, `estimated_minutes` is the subtree
sum (the parent's own estimate is ignored) and `workflow_status` rolls up (all
done → done, all open → open, otherwise in-progress). Such a parent's status is
read-only — a direct status write is refused with a `409`. Children flow the other
way: a new subtask **seeds** its priority and due date from its parent as
overridable defaults (create-time only; changing the parent later never clobbers
existing children — same rule as project inheritance).

### The most important table

```
ai_training_examples
- id
- task_name          (e.g. "extract_tasks", "match_project")
- input_text         (raw input, exactly as the model saw it)
- model_output_json  (full model output, not just the diff)
- corrected_output_json
- accepted           (bool)
- model_profile      (e.g. "task_extraction")
- model_name         (e.g. "gemma4:e2b")
- created_at
```

This collects fine-tuning data automatically as you correct AI outputs. **Do not skip storing the full input and full output** — the diff alone is useless for training later.

## Model gateway

Never call Ollama directly from workflow code. Always go through the gateway:

```
Workflow → ModelGateway → Provider → Ollama / llama.cpp
```

This is the single most important architectural decision in the project. It means Sprint 0 code keeps working when the custom-trained model arrives.

### Model profiles (v1, Ollama)

```yaml
task_extraction:
  provider: ollama
  model: gemma4:e2b           # starting model; benchmark e4b / other sizes on your data later
  temperature: 0
  max_tokens: 1024
  response_mode: json_schema
  system_prompt: extract_tasks.md

project_matching:
  provider: ollama
  model: gemma4:e2b
  temperature: 0
  max_tokens: 1024
  response_mode: json_schema
  system_prompt: match_project.md

summary:
  provider: ollama
  model: gemma4:e2b
  temperature: 0.2
  max_tokens: 2048
  response_mode: text
  system_prompt: summarize_project.md
```

### Model profiles (v2, custom llama.cpp)

```yaml
task_extraction:
  provider: llamacpp
  model: task-extractor-v1.Q4_K_M.gguf
  base_url: http://localhost:8080/v1
  temperature: 0
  max_tokens: 768
  response_mode: json_schema
  system_prompt: extract_tasks.md
```

## AI workflow

```
Raw inbox text
→ hash input + check for duplicates (idempotency)
→ save inbox item
→ call task extraction model via gateway
→ validate JSON with Pydantic
→ create task rows with review_status="candidate"
→ user reviews in UI
→ accepted candidates flip review_status to "accepted"
→ correction (original vs final) saved to ai_training_examples
```

## Task extraction schema

```json
{
  "summary": "string",
  "project_hint": "string|null",
  "tasks": [
    {
      "title": "string",
      "description": "string|null",
      "due_date": "YYYY-MM-DD|null",
      "priority": "low|medium|high|urgent",
      "assignee_hint": "string|null",
      "confidence": 0.0
    }
  ],
  "needs_review": true
}
```

## Sprint plan

**Where we are:** Sprints 0–24 shipped. The planning-view epic (Gantt/calendar,
Sprints 17–24) was built and then **removed** — the Gantt didn't earn its
complexity. There is **no committed next epic**: the active work is the
cleaning/hardening pass in `TODO.md`, and `CURRENT.md` tracks the current focus
(currently none).

Completed work is logged sprint-by-sprint in `DONE.md`; current-sprint notes live
in `CURRENT.md`; the backlog is in `TODO.md`. This section is the plan, not the
changelog — don't reproduce shipped-work prose here.

Still on the roadmap (not yet started):

```
Sprint 10: Export ai_training_examples → Unsloth fine-tune → llama.cpp swap
           (gated on 200+ training examples — the /training meter tracks this)
```

## First vertical slice

Build this end-to-end before anything else:

```
React command center or inbox page
POST /api/inbox                    (creates inbox_item)
POST /api/inbox/{id}/process       (runs extraction workflow)
Ollama call through ModelGateway
Pydantic validation
Task rows saved with review_status="candidate"
Review UI lists candidates directly under the messy-text capture box
Accept candidate → review_status="accepted"
Diff saved to ai_training_examples
```

If this works, everything else is incremental.

## Cross-cutting requirements (set up in Sprint 0)

- **Structured logging with request IDs.** Every request gets an ID; every log line in its lifecycle carries it. When an AI workflow misbehaves, you'll trace one inbox item from POST → extraction → validation → candidate creation. `structlog` does this in ~20 lines of config.
- **Alembic from day one.** Schema changes without migrations on a database you actually use is painful. `alembic init` in Sprint 0.
- **Idempotency.** Hash inbox input text. Re-processing the same input shouldn't create duplicate candidates.
- **Soft deletes.** `deleted_at` column on every user-facing table.
- **Backups.** Even just a nightly `sqlite3 .backup` cron is fine. Set it up before you have data you care about losing.
- **Eval harness.** Five hand-written cases in `extraction_cases.yaml` and a script that runs them on prompt changes. Doesn't need to be fancy.

## Settings UI

A page (Sprint 5, overhauled Sprint 9g) with a sectioned card UI (Profiles ·
Prompts · Evals) that lets you:
- Edit model profiles — model (dropdown of installed Ollama models, free-text
  fallback), temperature, max_tokens — with dirty-state, save confirmation, and
  reset-to-default for any local override
- Edit prompts in `ai/prompts/*.md` without restarting — monospace editor with a
  live char count, revert-to-last-saved, and the workflow each prompt feeds
- Trigger a re-run of evals (per suite: `task_extraction` / `project_matching` /
  `summary`, or all at once) and see a pass-rate trend across recent runs
- Check live Ollama health (reachable / host) with a re-check button

Profile edits write to **`backend/app/ai/profiles.local.yaml`** (gitignored), which the gateway
deep-merges over the committed `profiles.yaml` (local wins per-field). The committed file is
never touched, so your tuning stays local and the defaults stay in git. The gateway's profile
cache is cleared on each save, so changes take effect without a restart.

This pays for itself the first time you tune a prompt.

Settings mutation routes are intentionally localhost-only: profile saves,
profile-override resets, prompt saves, and eval runs mutate local files or run
local model work, so LAN clients receive `403` for those writes. Read-only
Settings routes — including the Ollama health (`/ollama/status`) and
installed-models (`/models`) introspection — can still be used from another
device when the API is bound to `0.0.0.0`. The loopback check assumes a direct
bind; reverse-proxy deployments need explicit trusted-proxy handling before
forwarding Settings writes.

## Do not build yet

```
Custom models           (wait for real training data, ~200+ examples)
Discord buttons
Calendar sync
Obsidian integration
Email ingestion
Multi-user auth
Celery / Redis
Vector DB
Autonomous agents
```

## Dev commands

```
./main.sh                 # bootstrap env/deps, migrate, start Ollama + backend + frontend
                          # and start Discord when DISCORD_BOT_TOKEN +
                          # BACKEND_SHARED_SECRET are set
./test.sh                 # backend pytest/ruff/mypy + frontend Vitest/lint/build
./test.sh --ai-evals      # also run the Ollama-backed AI eval suites
./scripts/backup_db.sh    # snapshot data/app.db → data/backups/
```

`main.sh` creates missing `backend/.env` and `frontend/.env` from the example
files, creates `backend/.venv` when needed, installs existing declared
dependencies when local installs are missing, runs Alembic migrations, and keeps
all dev processes in the foreground until `Ctrl-C`. It binds through the existing
`.env` settings: `API_HOST` defaults to `127.0.0.1` for the backend, and
`DEV_HOST` defaults to `127.0.0.1` for Vite.

Backend dependencies are pinned for reproducibility: minimum versions live in
`backend/pyproject.toml`, and exact, fully-resolved versions live in the committed
`backend/requirements.lock`. Both `main.sh` and `test.sh` install with
`pip install -e '.[dev]' -c requirements.lock` so a fresh `.venv` gets the locked
versions (the frontend equivalent is `package-lock.json` + `npm ci`). After
intentionally bumping a dependency, regenerate the lock from the updated venv:

```
cd backend && .venv/bin/python -m pip freeze --exclude-editable > requirements.lock
```

AI evals are opt-in for `test.sh` because they require Ollama and the configured
local model. The default quality gate stays deterministic and does not hide known
frontend flakes by skipping tests.

When `API_HOST=0.0.0.0`, this is intentionally a single-user, trusted-LAN app.
Normal project/task/inbox/trash/training routes are reachable from LAN clients
for both reads and writes. Settings writes remain localhost-only and return
`403` from non-loopback clients, and Discord routes are protected by
`BACKEND_SHARED_SECRET`. This is not multi-user auth; revisit real auth if the
app is exposed beyond a trusted home LAN.

The two routes that call Ollama — `POST /api/discord/inbox` and
`GET /api/projects/{id}/summary` — are per-IP rate limited (in-process, no
external dependency) to cap runaway model work. Tune via
`RATE_LIMIT_DISCORD_INBOX_PER_MIN` (default 30) and `RATE_LIMIT_SUMMARY_PER_MIN`
(default 20); a breach returns `429` with a `Retry-After` header.

### Backups (Sprint 6)

`scripts/backup_db.sh` takes a consistent snapshot of `data/app.db` into
`data/backups/` and prunes snapshots older than `BACKUP_RETENTION_DAYS` (default 14).
It uses Python's stdlib `sqlite3.Connection.backup()` — a proper online backup (safe
on a live DB, not a torn file copy), with no external `sqlite3` CLI dependency.
Schedule it with cron:

```
0 2 * * * /path/to/project-command-center/scripts/backup_db.sh
```

## Discord setup (Sprint 3)

The bot is a separate process that calls the API over HTTP. To run it:

1. **Create the app + bot** at https://discord.com/developers/applications → New
   Application → Bot. Copy the bot token (shown once).
2. **Set env vars** in `backend/.env`:
   - `DISCORD_BOT_TOKEN` — the token from step 1.
   - `BACKEND_SHARED_SECRET` — any long random string (e.g.
     `python -c "import secrets; print(secrets.token_urlsafe(32))"`). Empty disables
     the `/api/discord/inbox` route (returns 503). Backend and bot read the same `.env`.
   - `DISCORD_GUILD_ID` (optional) — your server's ID. Set it for **instant** slash-command
     registration during testing; without it, global sync can take ~an hour to appear.
3. **Invite the bot**: OAuth2 → URL Generator → scopes `bot` + `applications.commands`,
   permission `Send Messages`. Open the URL, pick your server, authorize.
4. **Run** the three processes above. In Discord: `/inbox <messy text>` → the bot replies
   with extracted task titles; the candidates appear in the web app's inbox
   **"Awaiting review"** list to accept/reject.

> The API binds to `API_HOST` (loopback by default). The shared secret — not the bind
> address — is what protects the discord route, so it stays safe even when the API is
> exposed on the LAN.

## North star

A **boring, reliable local app** where AI is a helper, not the boss:

```
React UI
+ FastAPI app core
+ SQLite truth
+ small local model calls through a gateway
+ Pydantic validation
+ review queue
+ training data collection
+ eventual custom llama.cpp models trained on your own corrections
```

That's the blueprint.
