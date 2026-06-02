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
- [x] `backend/app/db/models.py` — add `InboxItem`, `AITrainingExample` models + migration
- [x] `backend/app/services/inbox.py` — save inbox item, SHA-256 hash for idempotency check
- [x] `backend/app/services/training_data.py` — write correction to `ai_training_examples`
- [x] `backend/app/ai/schemas.py` — Pydantic v2 schemas for extraction input/output
- [x] `backend/app/ai/profiles.yaml` — `task_extraction`, `project_matching`, `summary` profiles
- [x] `backend/app/ai/providers/base.py` — abstract `BaseProvider` with `complete()` method
- [x] `backend/app/ai/providers/ollama.py` — Ollama HTTP provider (uses `httpx`, no `import ollama`)
- [x] `backend/app/ai/gateway.py` — loads profile by name, routes to correct provider
- [x] `backend/app/ai/prompts/extract_tasks.md` — extraction system prompt
- [x] `backend/app/ai/workflows/extract_tasks.py` — full workflow: hash → save → call gateway → validate → create candidates
- [x] `backend/app/api/routes_inbox.py` — `POST /api/inbox`, `POST /api/inbox/{id}/process` (+ `GET` list/one/candidates, `POST /{id}/review`)
- [x] Pydantic validation failure: log raw output + save to `ai_training_examples` as failure case
- [x] Idempotency: same input hash returns existing inbox item, no re-extraction
- [x] Happy-path pytest for extraction workflow (mock the gateway)
- [x] `backend/app/ai/evals/extraction_cases.yaml` — 5 hand-written test cases
- [x] `backend/app/ai/evals/run_evals.py` — script that runs cases and prints pass/fail

### Frontend — Inbox & review queue
- [x] `src/api/inbox.ts` — typed fetch wrappers for inbox endpoints
- [x] `src/features/inbox/InboxPage.tsx` — textarea to paste messy text, submit button
- [x] `src/features/inbox/ReviewQueue.tsx` — lists candidate tasks from a processed inbox item
- [x] Accept/reject candidates — via batch `POST /api/inbox/{id}/review` (supersedes the
      per-candidate `PATCH`/`DELETE` wording; one atomic call applies all decisions)
- [x] On review: corrections written to `ai_training_examples` as **one** row (full input + output
      + corrected output)
- [x] End-to-end manual test: paste text → process → review → accept some → reject some → check DB
      (see TASKS_SPRINT_2.md "Done check" — verified via DB on inbox #5)

---

## Sprint 3 — Discord Bot
> Goal: `/inbox` slash command in Discord triggers the same extraction workflow.

- [x] `backend/app/api/routes_discord.py` — `POST /api/discord/inbox` (shared-secret auth)
- [x] API bind: kept at `api_host` default (loopback); shared secret is the route's
      protection since the user runs `0.0.0.0` for LAN (explicit override of the 127.0.0.1 rule)
- [x] `BACKEND_SHARED_SECRET` in `.env`, validated (`hmac.compare_digest`) on every discord
      route request; empty secret disables the route (503)
- [x] `backend/app/integrations/discord/bot.py` — discord.py bot, separate process
- [x] `backend/app/integrations/discord/commands.py` — `/inbox` slash command, calls backend over HTTP
- [x] Bot replies with extraction summary (task titles + project hint)
- [x] Manual test: `/inbox "finish firewall cleanup by Friday"` → candidates appear in app
      (verified on a real guild; reviewed via the new inbox "Awaiting review" list)
- [x] Web inbox shows a pending-review queue (`GET /api/inbox`) so out-of-band (Discord)
      captures are reviewable; zero-candidate notes can be dismissed; returns to main screen
      after review. (Not in the original plan — added when Discord capture exposed the gap.)

---

## Sprint 4 — Project Matching
> Goal: extracted tasks get automatically matched to existing projects using aliases.

- [x] `backend/app/db/models.py` — add `ProjectAlias` model + migration
      (also added `inbox_items.suggested_project_id` + `match_input_text`/`match_output_json`/
      `match_model_name` to persist the match suggestion and model I/O)
- [x] `backend/app/ai/prompts/match_project.md` — matching system prompt
- [x] `backend/app/ai/workflows/match_project.py` — deterministic alias lookup first, AI
      fallback on a miss with a Python guard (returned `project_id` must be one offered).
      Matches per **inbox item** via `project_hint` (the extraction schema has one hint per
      note, not per task); candidate task titles are passed to the model as context. Non-fatal:
      a match failure never loses the extracted tasks.
- [x] `backend/app/services/projects.py` — alias CRUD + `match_text_to_project`
      (searches the note's hint + summary + raw text + task titles, so an alias in the
      body matches even when the extractor produced no hint) + `list_projects_with_aliases`
- [x] Matching workflow called after extraction (in `routes_inbox.process`, best-effort);
      `project_id` set on accepted candidates at review (inherits the suggestion, overridable)
- [x] `backend/app/api/routes_projects.py` — CRUD for aliases (`/projects/{id}/aliases`)
- [x] Manual test: inbox text mentions a project by alias → task lands in correct project
      (deterministic alias match on the note's `project_hint`)
- [x] Eval: `backend/app/ai/evals/match_cases.yaml` + `run_match_evals.py` (CLAUDE.md: every
      workflow has an eval case)

Scope extensions agreed with the user (beyond the original list above):
- [x] ReviewQueue project-override dropdown — shows the matched project, overridable per task
      (`ReviewEdit.project_id`; frontend `ReviewQueue`/`useInbox`/`InboxPage`)
- [x] Match-correction training capture — overriding an **AI** suggestion writes a
      `project_matching` row to `ai_training_examples` (prime directive #4). Deterministic
      alias hits have no model output, so they capture nothing.

---

## Sprint 5 — Dashboard & Settings UI
> Goal: useful overview page and a settings panel for tuning AI without restarting.

### Dashboard
- [x] `backend/app/api/routes_ai.py` — `GET /api/dashboard` (counts, no model) + `GET /api/projects/{id}/summary` (calls summary workflow)
- [x] `backend/app/ai/prompts/summarize_project.md` — summary system prompt
- [x] `backend/app/ai/workflows/summarize_project.py` — summarize a project's open tasks
- [x] `backend/app/services/dashboard.py` — aggregation service (open-task counts, recent inbox)
- [x] `backend/app/schemas/dashboard.py` — `DashboardRead`, `ProjectOpenTasksRow`, `ProjectSummaryRead`
- [x] `src/types/dashboard.ts` + `src/api/dashboard.ts` + `src/features/dashboard/useDashboard.ts`
- [x] `src/features/dashboard/DashboardPage.tsx` — open tasks count, recent inbox items, per-project summaries with on-demand Summarize button
- [x] `backend/app/ai/evals/summary_cases.yaml` + `run_summary_evals.py` — 3 eval cases
- [x] `backend/tests/test_routes_ai.py` + `test_summary_workflow.py` — 9 tests (all passing)

### Settings
- [x] `backend/app/api/routes_settings.py` — GET/PATCH profiles, GET/PUT prompt files, trigger eval run
- [x] `src/features/settings/SettingsPage.tsx` — switch active profile, edit prompt text, tune temp/tokens
- [x] Prompt edits write to `ai/prompts/*.md` on disk (not in DB); profile edits write to
      gitignored `profiles.local.yaml`, deep-merged over committed `profiles.yaml` (untouched)
- [x] "Run evals" button calls backend → runs the suite's `run()` → returns pass/fail counts
      (`run_evals.py`/`run_match_evals.py`/`run_summary_evals.py` each expose `run()`)

---

## Sprint 6 — Hardening & Backups
> Goal: the app is reliable enough to trust with real data.

- [x] Nightly SQLite backup — `scripts/backup_db.sh` (stdlib `sqlite3.Connection.backup()`
      online snapshot + 14-day prune, cron line in README; no external CLI dependency)
- [x] `activity_events` model + migration — append-only audit log (no `deleted_at`,
      documented exception); migration `09002cc3cb7c`
- [x] `backend/app/services/activity.py` — `record_event`/`list_events`, called from
      `services/projects.py` + `services/tasks.py` (task events guarded on `project_id`)
- [x] `src/features/projects/ActivityFeed.tsx` — per-project feed on the tasks page
      (`GET /api/projects/{id}/activity`, `useProjectActivity` hook, refreshes on task change)
- [x] Expanded eval suite — 20 cases in `extraction_cases.yaml` (was 7)
- [ ] `docker-compose.yml` — backend + frontend in containers (**deferred**: "clean
      restarts, not prod"; not needed to trust the app with data — the one open box)
- [x] README updated: backup script + cron, activity-log schema note, Sprint 6 status
- [x] Full manual smoke test of the entire flow, top to bottom (project/task lifecycle
      verified in the browser; AI inbox→process→accept path verified live against Ollama
      — accepted candidate logs a `created` task event in the feed)

Hardening additions (Codex review pass):
- [x] Atomic workflow commits — service layer uses `flush()` only; workflows own `commit()`;
      rollback on any exception; no partial writes on extraction or review
- [x] DB-backed inbox idempotency — `uq_inbox_items_active_input_hash` partial unique index
      (`deleted_at IS NULL`); `create_inbox_item` catches `IntegrityError` for race-safety;
      soft-deleted rows no longer block re-submission; migration validates no existing dupes
- [x] General project — protected `system_key="general"` seed (idempotent migration);
      `is_protected` property guards soft-delete; active tasks rehomed to General on project
      delete; `_default_project_id_for_status` auto-files accepted/done tasks
- [x] Global task view — `GET /api/tasks` + `POST /tasks` (unscoped, lands in General);
      `/tasks` frontend route; `useTasks` dispatches to scoped or global endpoint
- [x] Settings writes localhost-only — `require_local_settings_write` dependency on profile
      `PATCH`, prompt `PUT`, and eval `POST`; LAN clients get 403; reads remain public
- [x] Server-side pending inbox endpoint — `GET /api/inbox/pending?limit=N`; filtering and
      newest-first ordering moved from frontend to backend service layer
- [x] Dashboard grouped aggregate queries — single `GROUP BY` for per-project task counts;
      batch project resolution for recent inbox items; zero-task projects still appear
- [x] Blank-string input validation — `NonBlankStr` / `OptionalStrippedStr` in
      `schemas/common.py`; applied to project names, aliases, task titles, inbox text,
      Discord text, and review edits; optional blank fields normalize to null
- [x] Discord processing matches web — `routes_discord` calls `match_workflow.match_inbox_item`
      after extraction; match failure is non-fatal, matching web inbox behavior
- [x] Frontend Vitest smoke tests — Vitest + jsdom + Testing Library + jest-dom wired via
      Vite config; `npm run test`; inbox review flow smoke test (load, edit, reject, submit)

---

## Sprint 7 — Daily-Use & Polish
> Goal: make the app a daily driver.

### Daily-use slice (highest priority — makes it a real daily driver)
- [x] Global / cross-project task view — "everything on my plate" sorted by due date
      (top-level `/tasks` shows accepted work across projects)
- [ ] Overdue / due-soon highlighting in the global view
- [ ] Inline task editing in the task list — status / priority / due-date / description
      (confirm current page isn't create-only; gap before estimates/dependencies)
- [ ] Edit project info from the UI — frontend slice over existing `PATCH /api/projects/{id}`

### Capture-hygiene slice
- [ ] Clear / dismiss items from the recent inbox view — soft-delete on `inbox_items`
      (DELETE route + UI). MUST NOT cascade-delete the item's `ai_training_examples`
      rows — those are accounting data; just hide the inbox row.
- [ ] Trash / restore view — surface the soft-delete safety net in the UI
      (recently deleted projects/tasks → restore)
- [ ] Alias management UI — add/remove aliases on the project edit page over the
      existing Sprint 4 alias CRUD endpoints; directly feeds match accuracy

### Task-model slice (separate PRs — do not bundle)
- [ ] Task nesting — nullable `parent_task_id` FK on `tasks` + Alembic migration;
      `list_subtasks()` helper in `services/tasks.py`; cycle-detection guard (no A→B→A);
      nested display in task list with indent; create-subtask from parent task context
- [ ] Task duration estimate — nullable `estimated_days` integer column + migration;
      UI shows human labels (1 day / 3 days / 1 week / 2 weeks); feeds task-dependency
      scheduling and future kanban / calendar auto-layout (do not build those yet)
- [ ] Task dependencies — self-referential `task_dependencies` table + migration
      + Python cycle-detection guard (prime directive #1: app owns the logic, no
      "blocked" status without a guard preventing A→B→A cycles)

### Default "General" project
- [x] Seed a default "General" project (idempotent migration, stable slug not id)
- [x] Make it un-deletable (guard in `services/projects.py` — soft-delete must not
      orphan capture)
- [x] Decide: deleting a non-General project rehomes its active tasks to "General";
      the global task view keeps accepted work reachable even when project context is weak.

### Visibility slice
- [ ] Training-data viewer + progress meter — read-only page: row count, progress bar
      to 200, and input / output / corrected triples. The app's whole reason for
      existing currently has no UI surface; also tells us when fine-tuning is viable.
- [ ] Eval history — persist pass/fail per eval run (tiny table) so prompt edits can
      be seen to help or regress over time

### UI polish (split into small PRs, not one redesign)
- [ ] Real top-level navigation — sidebar or top nav linking Dashboard / Inbox / Projects / Tasks / Settings
- [ ] Consistent empty / loading / error states across pages
- [ ] Toasts for success / failure
- [ ] Shared component layer in `src/components/` — Button, Card, Badge, Modal
- [ ] Visual hierarchy — card-based layout, readable type scale, subtle color accents for status/priority
- [ ] Mobile-responsive layout (accessed from LAN devices)

---

## Sprint 8 — Custom Model Training
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
- [ ] `docker-compose.yml` — backend + frontend in containers (deferred from Sprint 6)
