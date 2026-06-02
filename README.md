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
      components/
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
project_aliases
tasks                  (includes status: candidate | accepted | rejected | done)
inbox_items            (includes input_hash for idempotency)
activity_events
eval_runs
ai_training_examples
```

All tables use **soft deletes** via a `deleted_at` column. Don't actually delete rows — you'll change your mind, and training data references them.

> **Exception:** `activity_events` (Sprint 6) is an append-only audit log and has
> **no** `deleted_at` — an audit trail is never user-edited. It records
> project/task lifecycle changes (created/updated/completed/deleted) from the
> service layer and feeds the per-project ActivityFeed.
>
> **Exception:** `eval_runs` (Sprint 7) is the same kind of append-only run log
> (one row per eval-suite run: `suite`, `passed`, `total`) and likewise has **no**
> `deleted_at`. It lets prompt/profile edits be judged as helping or regressing over
> time; surfaced as run history on the Settings page.

Tasks use a `status` enum rather than a separate `task_candidates` table. Candidates and real tasks live in the same table, distinguished by status. Simpler queries, no sync logic. Split later if it ever becomes painful.

A protected `General` project is seeded with the stable system key `general`.
Deleting any other project rehomes its active tasks to `General` before the
project is soft-deleted, and the top-level `/tasks` view lists accepted work
across projects so dashboard counts always point to reachable tasks.

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
→ create task rows with status="candidate"
→ user reviews in UI
→ accepted candidates flip status to "accepted"
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

```
Sprint 0:  [DONE] Repo setup, FastAPI skeleton, Alembic, structlog,
           React+Vite scaffold, config, health endpoint, .env handling
Sprint 1:  [DONE] Projects + tasks CRUD, soft deletes, basic React pages
Sprint 2:  [DONE] Inbox + ModelGateway + Ollama provider + extraction workflow
           + Pydantic validation + review queue UI
           + eval cases in extraction_cases.yaml (7/7 on gemma4:e2b)
Sprint 3:  [DONE] Discord /inbox command — shared-secret route + discord.py bot
           (set BACKEND_SHARED_SECRET to enable POST /api/discord/inbox)
Sprint 4:  [DONE] Project matching — deterministic alias lookup first, AI fallback
           (project_matching profile) guarded so the model can't invent a project.
           Suggestion stored on the inbox item, applied to accepted candidates at
           review (overridable). Overriding an AI suggestion → ai_training_examples.
Sprint 5:  [DONE] Dashboard — GET /api/dashboard (instant counts) +
           GET /api/projects/{id}/summary (on-demand AI prose, 502-safe).
           DashboardPage with per-project Summarize button; recent inbox links
           resolve to the project tasks actually filed to (not just the suggestion).
           summary eval suite (run_summary_evals.py).
           Settings UI — edit model profiles (write to gitignored profiles.local.yaml,
           deep-merged over the committed profiles.yaml; reload, no restart), edit
           ai/prompts/*.md on disk, and trigger eval runs (synchronous, pass/fail counts).
Sprint 6:  [DONE] Hardening — append-only activity_events log (project/task
           changes, surfaced as a per-project ActivityFeed on the tasks page);
           nightly SQLite backup script (scripts/backup_db.sh + cron); extraction
           eval suite expanded to 20 cases; atomic workflow commits; DB-backed
           inbox idempotency (partial unique index); General project (protected,
           rehomes tasks on project delete); global GET /api/tasks + /tasks UI;
           settings writes localhost-only; server-side pending inbox endpoint;
           dashboard grouped aggregate queries; blank-string input validation;
           Discord processing matches web inbox (project matching included);
           frontend Vitest smoke tests. docker-compose deferred.
Sprint 7:  [WIP] Daily-use & polish. Done: daily-use slice (global task view,
           overdue/due-soon highlighting, inline task + project editing), General
           project. Visibility slice: training-data viewer + progress-to-200 meter
           (read-only GET /api/training-examples + /stats, /training page) and
           eval-run history (append-only eval_runs table, persisted on each Settings
           eval run, GET /api/settings/evals/runs, shown on the Settings page).
           Capture-hygiene (in progress): dismiss/clear inbox items
           (DELETE /api/inbox/{id} soft-delete + per-item Dismiss button; training
           examples preserved, no migration); alias management UI (add/remove
           aliases in the project edit modal over the Sprint 4 alias endpoints,
           frontend-only).
Sprint 8:  Export ai_training_examples → Unsloth fine-tune → llama.cpp swap
           (gated on 200+ training examples — the /training meter tracks this)
```

## First vertical slice

Build this end-to-end before anything else:

```
React inbox page
POST /api/inbox                    (creates inbox_item)
POST /api/inbox/{id}/process       (runs extraction workflow)
Ollama call through ModelGateway
Pydantic validation
Task rows saved with status="candidate"
Review UI lists candidates
Accept candidate → status="accepted"
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

A small page (Sprint 5) that lets you:
- Edit model profiles — model, temperature, max_tokens
- Edit prompts in `ai/prompts/*.md` without restarting
- Trigger a re-run of evals (per suite: `task_extraction` / `project_matching` / `summary`)

Profile edits write to **`backend/app/ai/profiles.local.yaml`** (gitignored), which the gateway
deep-merges over the committed `profiles.yaml` (local wins per-field). The committed file is
never touched, so your tuning stays local and the defaults stay in git. The gateway's profile
cache is cleared on each save, so changes take effect without a restart.

This pays for itself the first time you tune a prompt.

Settings mutation routes are intentionally localhost-only: profile saves,
prompt saves, and eval runs mutate local files or run local model work, so LAN
clients receive `403` for those writes. Read-only Settings routes can still be
used from another device when the API is bound to `0.0.0.0`.

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
ollama serve
cd backend && python -m app.main   # reload on; binds API_HOST from .env (default 127.0.0.1, set 0.0.0.0 for LAN)
cd frontend && npm run dev   # binds DEV_HOST from .env (default 127.0.0.1, set 0.0.0.0 for LAN)
cd backend && python -m app.integrations.discord.bot   # Discord bot (needs DISCORD_BOT_TOKEN + BACKEND_SHARED_SECRET)
cd backend && python -m app.ai.evals.run_evals         # task_extraction eval cases (needs Ollama)
cd backend && python -m app.ai.evals.run_match_evals   # project_matching eval cases (needs Ollama)
cd backend && python -m app.ai.evals.run_summary_evals # project summary eval cases (needs Ollama)
./scripts/backup_db.sh                                 # snapshot data/app.db → data/backups/
```

When `API_HOST=0.0.0.0`, LAN clients can reach read APIs, but Settings writes
remain localhost-only and return `403` from non-loopback clients.

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
