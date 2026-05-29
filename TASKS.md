# Master Task List

Track progress across all sprints here. One line per task. Update status as you go.
When starting a sprint, copy the relevant tasks into a `TASKS_SPRINT_X.md` file for focused work.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Sprint 0 — Skeleton & Infrastructure
> Goal: every layer exists, wired together, and the health endpoint is live. No business logic.

- [x] `backend/app/main.py` — FastAPI app instance, mounts routers, runs health endpoint
- [x] `backend/app/config.py` — pydantic-settings, reads `.env`, exports a `get_settings()` function
- [x] `backend/app/logging_config.py` — structlog config, request-ID middleware wired into FastAPI
- [x] Alembic initialized — `alembic.ini` + `backend/app/alembic/env.py` pointing at `DATABASE_URL`
- [x] `GET /health` returns `{ "status": "ok", "env": "development" }`
- [x] `uvicorn app.main:app --reload` starts without errors
- [x] Frontend Vite scaffold runs — `npm run dev` opens the default page
- [x] Feature folder structure in place: `src/features/{dashboard,projects,tasks,inbox,settings}`
- [x] First commit on main: "Sprint 0 complete"

---

## Sprint 1 — Projects & Tasks CRUD
> Goal: create/read/update/delete projects and tasks through the API and basic React pages. No AI yet.

### Backend
- [x] `backend/app/db/models.py` — `Project`, `Task` SQLAlchemy 2.0 models with soft deletes (`deleted_at`)
- [x] `backend/app/db/session.py` — engine + `get_db` dependency
- [x] Alembic migration: initial schema (projects, tasks)
- [x] `backend/app/services/projects.py` — CRUD helpers, soft-delete filter baked in
- [x] `backend/app/services/tasks.py` — CRUD helpers, filter by project, soft-delete filter baked in
- [x] `backend/app/api/routes_projects.py` — GET list, GET one, POST, PATCH, DELETE (soft)
- [x] `backend/app/api/routes_tasks.py` — GET list (by project), GET one, POST, PATCH, DELETE (soft)
- [x] Happy-path pytest for each service (projects + tasks)

### Frontend
- [x] `src/api/projects.ts` — typed fetch wrappers for project endpoints
- [x] `src/api/tasks.ts` — typed fetch wrappers for task endpoints
- [x] `src/features/projects/` — project list page + create form
- [x] `src/features/tasks/` — task list page (scoped to a project) + create form
- [x] Basic routing in `src/routes/` — `/projects`, `/projects/:id/tasks`
- [x] End-to-end manual test: create project → create task → mark done → soft-deleted project disappears

---

## Sprint 2 — Inbox, Model Gateway, Extraction, Review Queue
> Goal: the full AI loop works end-to-end. This is the most important sprint.

### Backend — Inbox & models
- [ ] `backend/app/db/models.py` — add `InboxItem`, `AITrainingExample` models + migration
- [ ] `backend/app/services/inbox.py` — save inbox item, SHA-256 hash for idempotency check
- [ ] `backend/app/services/training_data.py` — write correction to `ai_training_examples`
- [ ] `backend/app/ai/schemas.py` — Pydantic v2 schemas for extraction input/output
- [ ] `backend/app/ai/profiles.yaml` — `task_extraction`, `project_matching`, `summary` profiles
- [ ] `backend/app/ai/providers/base.py` — abstract `BaseProvider` with `complete()` method
- [ ] `backend/app/ai/providers/ollama.py` — Ollama HTTP provider (uses `httpx`, no `import ollama`)
- [ ] `backend/app/ai/gateway.py` — loads profile by name, routes to correct provider
- [ ] `backend/app/ai/prompts/extract_tasks.md` — extraction system prompt
- [ ] `backend/app/ai/workflows/extract_tasks.py` — full workflow: hash → save → call gateway → validate → create candidates
- [ ] `backend/app/api/routes_inbox.py` — `POST /api/inbox`, `POST /api/inbox/{id}/process`
- [ ] Pydantic validation failure: log raw output + save to `ai_training_examples` as failure case
- [ ] Idempotency: same input hash returns existing inbox item, no re-extraction
- [ ] Happy-path pytest for extraction workflow (mock the gateway)
- [ ] `backend/app/ai/evals/extraction_cases.yaml` — 5 hand-written test cases
- [ ] `backend/app/ai/evals/run_evals.py` — script that runs cases and prints pass/fail

### Frontend — Inbox & review queue
- [ ] `src/api/inbox.ts` — typed fetch wrappers for inbox endpoints
- [ ] `src/features/inbox/InboxPage.tsx` — textarea to paste messy text, submit button
- [ ] `src/features/inbox/ReviewQueue.tsx` — lists candidate tasks from a processed inbox item
- [ ] Accept candidate → `PATCH /api/tasks/{id}` sets status to `accepted`
- [ ] Reject candidate → soft-delete via `DELETE /api/tasks/{id}`
- [ ] On accept/reject: diff written to `ai_training_examples` via service call
- [ ] End-to-end manual test: paste text → process → review → accept some → reject some → check DB

---

## Sprint 3 — Discord Bot
> Goal: `/inbox` slash command in Discord triggers the same extraction workflow.

- [ ] `backend/app/api/routes_discord.py` — `POST /api/discord/inbox` (shared-secret auth)
- [ ] API bound to `127.0.0.1` only in uvicorn config
- [ ] `BACKEND_SHARED_SECRET` in `.env`, validated on every discord route request
- [ ] `backend/app/integrations/discord/bot.py` — discord.py bot, separate process
- [ ] `backend/app/integrations/discord/commands.py` — `/inbox` slash command, calls backend over HTTP
- [ ] Bot replies with extraction summary (task titles + project hint)
- [ ] Manual test: `/inbox "finish firewall cleanup by Friday"` → candidates appear in app

---

## Sprint 4 — Project Matching
> Goal: extracted tasks get automatically matched to existing projects using aliases.

- [ ] `backend/app/db/models.py` — add `ProjectAlias` model + migration
- [ ] `backend/app/ai/prompts/match_project.md` — matching system prompt
- [ ] `backend/app/ai/workflows/match_project.py` — takes task + project list → returns best match
- [ ] `backend/app/services/projects.py` — add alias lookup helpers
- [ ] Matching workflow called after extraction; `project_id` set on accepted candidates
- [ ] `backend/app/api/routes_projects.py` — CRUD for aliases
- [ ] Manual test: inbox text mentions a project by alias → task lands in correct project

---

## Sprint 5 — Dashboard & Settings UI
> Goal: useful overview page and a settings panel for tuning AI without restarting.

### Dashboard
- [ ] `backend/app/api/routes_ai.py` — summary endpoint: `GET /api/projects/{id}/summary` (calls summary workflow)
- [ ] `backend/app/ai/prompts/summarize_project.md` — summary system prompt
- [ ] `backend/app/ai/workflows/summarize_project.py` — summarize a project's open tasks
- [ ] `src/features/dashboard/DashboardPage.tsx` — open tasks count, recent inbox items, per-project summaries

### Settings
- [ ] `backend/app/api/routes_settings.py` — GET/PATCH profiles, GET/PUT prompt files, trigger eval run
- [ ] `src/features/settings/SettingsPage.tsx` — switch active profile, edit prompt text, tune temp/tokens
- [ ] Prompt edits write to `ai/prompts/*.md` on disk (not in DB)
- [ ] "Run evals" button calls backend → runs `run_evals.py` → returns pass/fail

---

## Sprint 6 — Hardening & Backups
> Goal: the app is reliable enough to trust with real data.

- [ ] Nightly SQLite backup — cron or shell script using `sqlite3 .backup`
- [ ] `activity_events` model + migration — log project/task changes
- [ ] `backend/app/services/activity.py` — write activity events from service layer
- [ ] `src/features/projects/ActivityFeed.tsx` — shows recent activity per project
- [ ] Expanded eval suite — 20+ cases in `extraction_cases.yaml`
- [ ] `docker-compose.yml` — backend + frontend in containers (for clean restarts, not prod)
- [ ] README updated: setup steps, env vars, dev commands all verified accurate
- [ ] Full manual smoke test of the entire flow, top to bottom

---

## Sprint 7+ — Custom Model Training
> Do not start until you have 200+ rows in `ai_training_examples`.

- [ ] Export `ai_training_examples` to JSONL training format
- [ ] `training/unsloth/train_task_extractor.py` — Unsloth fine-tune script
- [ ] Evaluate fine-tuned model against eval suite
- [ ] `backend/app/ai/providers/llamacpp.py` — llama.cpp HTTP provider
- [ ] Update `profiles.yaml` to use `llamacpp` provider + new model
- [ ] Regression test: eval suite still passes with custom model
- [ ] Update README: note model swap, new dev commands

---

## Backlog / Nice-to-have (do not build until core is stable)
- [ ] litestream continuous replication instead of cron backups
- [ ] Task due-date reminders
- [ ] Keyboard shortcuts in review queue
- [ ] Bulk accept/reject in review queue
- [ ] Dark mode
- [ ] Export tasks to markdown
