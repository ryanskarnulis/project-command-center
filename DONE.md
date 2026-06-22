# Completed Tasks

All shipped work across sprints. Incomplete items live in `TODO.md`; current sprint notes in `CURRENT.md`.

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
- [x] Overdue / due-soon highlighting in the global view
- [x] Inline task editing in the task list — status / priority / due-date / description
      (modal dialog; `updateTask()` wired via `useTasks.update()`)
- [x] Edit project info from the UI — frontend slice over existing `PATCH /api/projects/{id}`
      (modal dialog; `updateProject()` wired via `useProjects.update()`)

### Capture-hygiene slice
- [x] Clear / dismiss items from the recent inbox view — soft-delete on `inbox_items`
      (`DELETE /api/inbox/{id}` + per-item Dismiss button on the Awaiting-review list).
      `ai_training_examples` are kept (no FK/cascade); freeing the active `input_hash`
      lets the same text be re-captured later. `dismiss_inbox_item` service +
      `useInbox.dismiss`; backend + Vitest tests.
- [x] Trash / restore view — surface the soft-delete safety net in the UI.
      Aggregate `GET /api/trash` (recently-deleted projects/tasks/inbox, newest
      first) + per-entity `POST .../restore` routes; `/trash` page with Restore
      buttons. `deleted()`/`restore()` helpers in `services/common.py`;
      `list_deleted_*`/`get_deleted_*`/`restore_*` per service. Restoring a
      dismissed inbox item whose text was re-captured since returns `409`
      (`RestoreConflictError`, active `input_hash` index would reject it); a
      restored task whose project is gone is rehomed to General. Backend + Vitest
      tests. No migration (`deleted_at` already exists).
- [x] Alias management UI — add/remove aliases in the project edit modal over the
      existing Sprint 4 alias CRUD endpoints (`GET/POST/DELETE
      /api/projects/{id}/aliases`); directly feeds match accuracy. Frontend-only
      (`listAliases`/`createAlias`/`deleteAlias` wrappers + alias section in
      `ProjectEditModal`, managed independently of name/description Save); Vitest test.

### Task-model slice (separate PRs — do not bundle)
- [x] Task nesting — nullable `parent_task_id` FK on `tasks` + Alembic migration
      (`f83c22ab757c`); `list_subtasks()` helper + `_assert_no_parent_cycle` guard
      (no A→B→A, no self-parent → `TaskCycleError` → 409) in `services/tasks.py`;
      `soft_delete_task` cascade-soft-deletes the subtree (restore stays per-task);
      nested/indented display (`.task-children`) with per-row "Add subtask" composer
      and a Parent-task dropdown (self+descendants excluded) in the edit modal.
- [x] Task duration estimate — nullable `estimated_minutes` integer column + migration
      (`d036d1c48a82`); Pydantic `gt=0` guard (0/negative → 422). UI shows human labels
      via `utils/duration.ts` (5/15/30 min, 1/2/4 hr, 1/3 day, 1/2 wk, 1 mo) — an
      "Estimate" dropdown in the edit modal + a `~label` badge in the task list. Feeds
      future task-dependency scheduling and kanban / calendar auto-layout (not built yet).
- [x] Task dependencies — `task_dependencies` table (two FKs to `tasks`, partial
      unique active-edge index) + migration (`3263531ae531`). Edge `A depends_on B`
      = B must be `done` before A starts; `services/task_dependencies.py` owns the
      logic: `add/remove/list_dependencies/list_dependents`, DFS cycle guard
      (self/duplicate/A→B→A → `DependencyError` → 409), `is_blocked` + bulk
      `blocked_task_ids`. "Blocked" is derived (no status column): `TaskRead.is_blocked`
      populated by the list/detail routes (one query, no N+1). Routes
      `GET/POST/DELETE /api/tasks/{id}/dependencies`. Frontend: `api/taskDependencies.ts`,
      `useTaskDependencies` hook, "Depends on" section in the edit modal (add/remove +
      done/pending state, inline 409 error), red **Blocked** badge in the task list.

### Default "General" project
- [x] Seed a default "General" project (idempotent migration, stable slug not id)
- [x] Make it un-deletable (guard in `services/projects.py` — soft-delete must not
      orphan capture)
- [x] Decide: deleting a non-General project rehomes its active tasks to "General";
      the global task view keeps accepted work reachable even when project context is weak.

### Visibility slice
- [x] Training-data viewer + progress meter — read-only `/training` page: row count,
      progress bar to 200, per-task breakdown, task/accepted filters, and input /
      output / corrected triples. Backend: `GET /api/training-examples` +
      `/api/training-examples/stats` (`services/training_data.list_examples` /
      `example_stats`, soft-delete aware), `routes_training.py`, `schemas/training.py`.
- [x] Eval history — append-only `eval_runs` table (no `deleted_at`, documented
      exception; migration `61ed365bec4c`); each Settings eval run persists a row via
      `services/eval_history.record_run`; `GET /api/settings/evals/runs` (read-only) +
      history shown per suite on the Settings page so prompt/profile edits can be seen
      to help or regress over time.

### UI refresh slice
- [x] Command-center shell — persistent sidebar/topbar layout in `AppShell`, lucide icons,
      responsive command-center styling, and disabled placeholders for not-yet-built tools
      (AI command search, timer, calendar) without adding backend scope.
- [x] Dashboard redesign — Focus Now cards for open tasks, awaiting review, blocked tasks,
      and due-soon work; project overview, AI insights, and calendar placeholder
      use existing API data only.
- [x] Contextual creation controls — removed the Quick Actions card; Open Tasks carries
      the add-task plus affordance, and Projects Overview carries the create-project
      plus affordance.
- [x] Dashboard capture workflow — reusable `InboxCapturePanel` embeds messy-text AI task
      extraction at the top of the command center; extracted candidates render immediately
      underneath for accept/reject/edit approval. `/inbox` reuses the same panel.
- [x] Training progress stays separate on `/training`; no training-progress widget in
      Focus Now.
- [x] UI test coverage — dashboard capture flow, disabled placeholders, and sidebar
      navigation expectations covered with Vitest.

---

## Sprint 8 — UI Polish
> Split into small PRs, not one redesign. The Sprint 7 command-center revamp landed
> the shell, dashboard, and capture panel; this sprint finishes the remaining polish.

- [x] Real top-level navigation — command-center sidebar/topbar linking Dashboard / Projects / Tasks / Training / Trash / Settings; Inbox remains a route and is embedded as the dashboard capture workflow
- [x] Visual hierarchy — card-based layout, readable type scale, subtle color accents for status/priority
- [x] Replace generic quick actions with contextual section controls for adding tasks and projects
- [x] Mobile-responsive layout (accessed from LAN devices)

Revamp follow-up fixes (from review of the Sprint 7 revamp):
- [x] Dashboard: drop the duplicate pending-inbox fetch in `useDashboard` — the capture panel already loads pending items and reports the count via `onPendingCountChange`
- [x] Dashboard: replace the local `weekDistance` helper with the shared `dueStatus(due, 7)` from `utils/dates` (no duplicated date math)
- [x] Dashboard: remove the dead `status !== 'done'` guard on blocked tasks — `GET /api/tasks` returns accepted-only, so it never filtered anything
- [x] Dashboard: time-aware hero greeting (morning / afternoon / evening) instead of a hardcoded "Good morning"

### Task & Inbox UX overhaul (8 slices)

- [x] **Slice 1 — Ordering + nesting integrity** (FE-only): sort tasks by due date, then by
      priority (`urgent` > `high` > `medium` > `low`) as the tie-breaker; keep subtasks grouped
      under their parent at every level. Extend `utils/dates.compareByDue` → `compareTasks`
      (priority rank as secondary key, `id` last) + unit tests.
- [x] **Slice 2 — Nav + project-name links** (FE-only): remove **Tasks** from the `AppShell`
      sidebar `primaryNav`; in the global task view render the real project **name** (from the
      already-loaded `projects`) instead of `Project #{id}`. Update `AppShell.test.tsx`.
- [x] **Slice 3 — Subtask project inheritance** (BE + pytest): in `services/tasks.create_task`,
      when `parent_task_id` is set and `project_id is None`, inherit the parent task's
      `project_id` (before the accepted→General fallback). Happy-path pytest. No migration.
- [x] **Slice 4 — Customizable estimate input** (FE-only): replace the fixed `DURATION_OPTIONS`
      dropdown with a custom estimate input; later upgraded in Sprint 9 to natural text parsing.
- [x] **Slice 5 — Shared `TaskCard` component** (FE-only, foundational): `features/tasks/TaskCard.tsx`
      — clickable card rendering title / workflow status / priority / due badge / estimate /
      Blocked / project; navigates to task detail on click.
- [x] **Slice 6 — Task detail view + add/edit modal unification** (FE + small BE): route
      `/tasks/:taskId` → `TaskDetailPage`; add `GET /api/tasks/{id}/subtasks` (BE); Add task
      button opens a modal that reuses the task form (create mode).
- [x] **Slice 7 — Task filter** (FE-only): filter bar on the task view (workflow status,
      priority, project [global view], plus due-soon/overdue/blocked toggles), filtering
      client-side while preserving parent→child grouping.
- [x] **Slice 8 — Inbox = review-only, per-candidate approval** (BE + FE): drop the capture panel
      from `/inbox`; show notes awaiting review with candidates as `TaskCard`s; open a candidate
      → edit it → **Submit** (approve) or **Dismiss** (reject); finalize the note only when no
      `candidate`-status tasks remain. pytest for per-candidate + finalization; Vitest for inbox cards.

---

## Sprint 9 — Task Detail Workspace & Status Split
> Goal: stop leaking backend review lifecycle into normal task management.

### Backend state model
- [x] Split task state into `review_status` (`candidate` / `accepted` / `rejected`) and
      `workflow_status` (`open` / `in_progress` / `done`)
- [x] Alembic migration `9b2c1d7e4a6f` — rename old `tasks.status` to `review_status`, add
      `workflow_status`, migrate old `done` rows, and upgrade the live SQLite DB to head
- [x] Update schemas, task services, routes, dashboard counts, AI summaries, eval helper,
      extraction, review, and dependency logic for the split
- [x] Keep `blocked` derived from dependencies; dependency completion now checks
      `workflow_status == done`
- [x] Default task lists/dashboard counts to reviewed work that is not workflow-done

### Task detail UI
- [x] Remove the task detail page's full-width `Edit` button and modal-driven editing
- [x] Rebuild `/tasks/:taskId` as an inline-editable workspace with:
      title, description, workflow status, priority, due date, project, parent task, and estimate
- [x] Add saving/saved/error feedback and client-side empty-title validation
- [x] Hide backend-only `candidate` / `accepted` / `rejected` from normal task detail UI
- [x] Add polished header actions: mark done/reopen and delete
- [x] Upgrade dependencies into linked rows with done/pending chips and icon remove buttons
- [x] Upgrade subtasks into a dedicated section with `TaskCard`s and inline add-subtask
- [x] Add `TaskDetailPage.test.tsx` coverage artifact

### Friendly estimates
- [x] Add `parseDurationInput` / `formatDurationInput` in `utils/duration.ts`
- [x] Estimate inputs now accept natural text: `30m`, `45 min`, `2h`, `2 hours`, `1 day`,
      `1 week`, plain numbers as minutes, and empty / `none` to clear
- [x] Use the friendly estimate input in task detail, `TaskFormModal`, and the older `TaskEditModal`
- [x] Add duration parser test artifacts and update task UI test artifacts

### Docs
- [x] README documents `review_status`, `workflow_status`, derived blocked state, Sprint 9,
      and the Alembic upgrade command

---

## Sprint 9c — Rich Inline Subtask Form
> Goal: faster subtask capture with priority, due date, and estimate at creation time.

- [x] Expand inline subtask composer (TasksPage.tsx) with optional fields:
      priority (dropdown: urgent/high/medium/low), due_date (date picker), and
      estimated_minutes (friendly text: "30m", "2h", "1 day", reuses `parseDurationInput`)
- [x] Validation: bad estimate text shows inline error; title must be non-blank
- [x] "More options" button in TasksPage composer — hands off draft to TaskFormModal
      (pre-fills parent + typed values) for description/project/status fields
- [x] TaskFormModal create mode — now seeds title/priority/due_date/estimated_minutes
      from optional `defaults` prop (backward-compatible, guarded with `?.`)
- [x] TaskDetailPage subtask form — same rich inline form upgrade
- [x] CSS: `.task-subtask-fields` + `.task-subtask-actions` layout classes for
      compact field/button rows; fixed title input height (was setting 260px on
      a column-direction flex)
- [x] Backlog entry in README: Sprint 11 (backlog) for AI "break this down" feature

---

## Sprint 9d — Inbox Approval UX Overhaul
> Goal: make per-note candidate review reliable and friendly.

- [x] **Chunk 1 — Reappear bug fix** (BE + pytest): `GET /api/inbox/{id}/candidates`
      returned every active task for the note regardless of `review_status`; fixed by
      filtering to `candidate` status only. Regression test added.
- [x] **Chunk 2 — Candidate-mode editor + breadcrumbs** (FE): a candidate's `TaskCard`
      opens `/tasks/:id` in candidate-mode — **Approve** / **Dismiss** replace **Mark done**
      / **Delete**; subtasks/dependencies hidden. Breadcrumb `Inbox › Note review › <title>`.
      Note review is now an addressable `/inbox/:inboxId` route.
- [x] **Chunk 3 — Bulk Approve all / Dismiss all** (FE): note-review buttons that decide
      every remaining candidate at once via `POST /api/inbox/{id}/review`.
- [x] **Chunk 4 — Surface model signals** (FE): per-candidate `conf 0.xx` badge
      (candidate-only) + suggested-project chip; candidates sorted lowest-confidence-first.
- [x] **Chunk 5 — Polish bundle** (FE): "N remaining to review" counter, `confirm`
      before the destructive "Dismiss note", and a post-finalize "View filed tasks" link.
- [x] **Chunk 6 — URL-based note navigation** (FE): clicking a note routes to
      `/inbox/:id` (was local state), so browser-back returns to the inbox list; added a
      `← Inbox` breadcrumb on the note view.
- [x] **Chunk 7 — Repair stale frontend tests** (tests only): added missing
      `listCompletedTasks`/`reopenTask` to the `api/tasks` mocks; drove the "Done" view
      through `listCompletedTasks`; updated the dashboard test to the "Awaiting Review"
      metric card. Frontend 86/86, backend 148/148.

---

## Sprint 9e — Projects Tab UX Overhaul
> Goal: bring the Projects tab up to par with the Sprint 8–9d Tasks/Inbox polish.
> Frontend-only — no schema/migration, no new/changed API route.

- [x] **Chunk 1 — Detail hub skeleton + route**: new `/projects/:id` `ProjectDetailPage`
      (inline-editable name/description save-on-blur, `← Projects` breadcrumb, the
      project's tasks as `TaskCard`s, "View all tasks" → kept `/projects/:id/tasks`
      board, 404→`/projects`); the projects list now links to the hub.
- [x] **Chunk 2 — Hub sections**: AI summary (Summarize via `GET /api/projects/{id}/summary`,
      502-safe), activity feed (reused `ActivityFeed`), and alias list/add/remove —
      giving aliases their home before `ProjectEditModal` was retired.
- [x] **Chunk 3 — Cards + modal**: `ProjectCard` (reuses `.task-card`) in a `.project-grid`;
      New project + Edit via `ProjectFormModal` (create/edit); confirm-aware Delete for
      non-protected projects; retired `ProjectEditModal`.
- [x] **Chunk 4 — Counts/progress/status**: extracted `projectStatus` + `Tone` to
      `utils/projectStatus.ts` (dashboard now imports it); per-project open/done counts,
      a progress bar, and a status badge (Clear/On Track/Due Soon/At Risk/Blocked) from
      `listAllTasks()`/`listCompletedTasks()` grouped client-side.
- [x] **Chunk 5 — Search + sort**: client-side search (name/description) + sort
      (name / most open tasks / recently updated / recently created), Clear, and a
      no-match empty state (reuses `.task-filters`).
- [x] **Chunk 6 — Polish**: `.page-loading` / `.empty-state` / `role="alert"` errors
      across both pages; confirm-before-delete (`window.confirm`); breadcrumb/heading
      consistency.
- Verification: frontend 112/112 (Vitest) + `tsc -b && vite build` green; backend untouched.

---

## Sprint 9f — Trash Tab UX Overhaul
> Goal: bring `/trash` up to par with the Sprint 8–9e Tasks/Inbox/Projects polish.

- [x] **Chunk 1 — Backend: expose `deleted_at`** (BE + pytest): added `deleted_at: datetime | None = None`
      to `ProjectRead`, `TaskRead`, `InboxRead` schemas (serializes `null` for active rows; no migration —
      reads the existing `SoftDeleteMixin.deleted_at` column); mirrored `deleted_at?: string | null` in
      frontend types; pytest: trash items carry non-null `deleted_at`, active rows carry `null`.
- [x] **Chunk 2 — Card layout + icons + context badges + states** (FE): section headings with lucide icons
      (`FolderX` / `Trash2` / `Inbox`) + per-section count; trashed tasks render as `TaskCard`, projects
      as `ProjectCard` (`buildProjectStats`), inbox as a small `.task-card` + `.source-pill`; each card
      shows "Deleted {formatRelative(deleted_at)}" (new `utils/dates.ts` helper + unit test); `.page-loading`
      / `.empty-state` / `role="alert"` state parity; `NoNav` capture-phase wrapper prevents card links from
      navigating to deleted-item 404 pages; `cleanup` registered in `src/test/setup.ts`.
- [x] **Chunk 3 — Search + type filter** (FE): case-insensitive search over display label; type filter
      All / Projects / Tasks / Inbox (client-side, hides empty sections); "Clear" resets both; distinct
      "No items match your search." empty state; filter bar hidden when trash is empty.
- [x] **Chunk 4 — Nav count + bulk restore + restore feedback** (FE): `TrashCountContext` / provider
      (new `features/trash/TrashCountContext.tsx`, wrapped in `App.tsx`) fetches `getTrash()` once and
      exposes `count` + `refresh()`; live count badge in `AppShell` (hidden at 0); `restoreAll(kind, items)`
      in `useTrash` iterates per-item restores, tolerates inbox 409s, reports restored-vs-skipped in the
      notice; transient `notice` channel names the item and warns tasks rehome to General; fixed: reload's
      `.then` no longer clears a 409 error set by a failed action.
- [x] **Chunk 5 — Permanent delete (purge) + Empty trash** (BE + FE): `common.hard_delete(db, obj)`
      guard (refuses if `deleted_at is None` → 409); per-entity FK cleanup: tasks→dependency rows +
      soft-deleted subtree; projects→aliases + soft-deleted tasks + null `inbox_items.suggested_project_id`
      + null `activity_events.project_id`; inbox→detach/purge trashed candidate tasks; routes
      `DELETE /api/{projects,tasks,inbox}/{id}/purge` (404 absent / 409 active / 403 General) +
      `DELETE /api/trash` (empty trash, returns per-kind counts); frontend: per-card "Delete forever"
      (confirm) + "Empty trash" button (confirm); `.trash-danger` style; count badge refreshes on purge;
      pytest: purge removes row; purge of active row → 409; purge of General → 403; `ai_training_examples`
      rows survive; FK cleanup leaves no dangling dependency/alias/parent rows + clears the two nullable
      project FKs; empty-trash is idempotent. `ai_training_examples` left untouched (no FK). No Alembic
      migration (purge is DML, not a schema change).

---

## Sprint 9g — Settings Tab UX Overhaul
> Goal: bring `/settings` up to par with the Sprint 8–9f Tasks/Inbox/Projects/Trash polish.
> Shipped as 6 small chunks (committed `EEE`–`III` under the Sprint 9 label; tracked as 9g
> to avoid colliding with the README's Sprint 10 Unsloth-export work).

- [x] **Chunk 1 — Structural foundation: header, cards, section nav** (FE): real page header +
      one-line description matching the other pages; sticky section nav (Profiles · Prompts · Evals);
      each `<li>` editor (`ProfileEditor`, `PromptEditor`, eval rows) converted to the shared card look
      with per-section lucide icons; all existing handlers/behavior preserved (pure structure + styling,
      no new endpoints).
- [x] **Chunk 2 — Edit safety: dirty-state + save confirmation** (FE): each editor computes whether
      inputs differ from the loaded value — Save disabled when unchanged, "unsaved" dot when dirty;
      `beforeunload` guard (refresh / tab-close / external nav) fed by a page-level dirty map; transient
      inline "Saved ✓" on success (extended the per-item `ActionState` with a `saved` flag, no toast
      system). In-app route-change blocking deferred (needs a `createBrowserRouter` conversion).
- [x] **Chunk 3 — Prompt editor upgrades** (FE): workflow tag per prompt derived on the frontend from
      the loaded profiles' `system_prompt` (e.g. `extract_tasks.md → task_extraction`); monospace,
      resizable, taller textarea + live character count; revert-to-last-saved button (pairs with the
      chunk-2 dirty-state). No backend change.
- [x] **Chunk 4 — Eval trend + run-all** (FE): flat run list replaced with a compact pass-rate trend
      across the recent runs already loaded via `getEvalRuns` (keeps latest-run failing-case details);
      one "Run all suites" button runs `task_extraction`, `project_matching`, and `summary` in sequence
      (reuses `runEvals`) with per-suite progress. No new endpoint.
- [x] **Chunk 5 — Ollama introspection: health panel + model dropdown** (BE + FE): provider
      introspection added via the gateway only (no `import ollama` outside `app/ai/providers/`) — a
      health/ping (reachable + host) and an installed-models list (Ollama `/api/tags`), exposed as two
      read-only GET routes `GET /api/settings/ollama/status` + `GET /api/settings/models` (public, no
      write guard). FE: top-of-page health row (connected / host, re-check button, graceful "not
      reachable" state); `ProfileEditor` free-text model input replaced with a dropdown from
      `/api/settings/models` preselecting the current value, with a free-text fallback for not-yet-pulled
      / custom names — never silently re-defaults `task_extraction` off `gemma4:e2b`. New route test +
      pytest green.
- [x] **Chunk 6 — Reset-to-default for overrides** (BE + FE): service helper removes a profile's
      override key(s) from `profiles.local.yaml` and reloads, returning the new effective `ProfileRead`;
      exposed as `DELETE /api/settings/profiles/{name}/overrides` (optional `?field=` clears one field,
      no field clears all), guarded by `require_local_settings_write` (404 unknown profile, no-op safe
      when no override exists). FE: "Reset to default" control per profile, enabled only when
      `overridden_fields` is non-empty, wired through `useSettings`; on success inputs reflect the
      committed `profiles.yaml` value and the "(overridden)" tags clear (reuses chunk-2 save feedback).
      New route test + pytest green. No schema/migration.

## Sprint 9j — UX Foundation + Global Search
> Goal: shared component layer, toasts, async state, and global search.

- [x] Consistent empty / loading / error states — shared `AsyncState` component shipped;
      adopted on TasksPage. Remaining pages can adopt it incrementally.
- [x] Toasts for success / failure — `ToastProvider`/`useToast` shipped, retrofitted onto
      task/project/inbox mutation hooks.
- [x] Shared component layer in `src/components/` — Button / Card / Badge / AsyncState
      primitives shipped. `ProjectCard` could still adopt them.
- [x] **Global search** — `GET /api/search?q=` over projects/tasks/inbox, grouped dropdown
      in the topbar `CommandSearch`, keyboard nav, click-through. Input kept generic for
      command-bar slash-action follow-up.

---

## Sprint 9i — Training-Data Pruning (trash → purge)
> Goal: let the user clean junk rows out of the corpus, but only via the same reversible two-step
> path (soft-delete → trash → purge) as projects/tasks/inbox. User-approved exception to "treat
> training data like accounting data" — the active corpus is never bulk-deleted.

- [x] **Chunk A — Backend delete/restore/purge** (BE): `services/training_data.py` gains
      `get_example`, `get_deleted_example`, `soft_delete_example`, `list_deleted_examples`,
      `restore_example`, `purge_example` (leaf table, so purge is a bare `hard_delete` and restore
      has no uniqueness conflict). Three routes on `/training-examples`: `DELETE /{id}` (soft-delete →
      trash, 204), `POST /{id}/restore`, `DELETE /{id}/purge` (404 absent / 409 active-not-trashed,
      mirroring inbox). `deleted_at` added to `TrainingExampleRead` (reads the existing
      `SoftDeleteMixin` column — no migration). A trashed row drops out of the `/training` list AND
      `example_stats` automatically (both already filter `deleted_at IS NULL`).
- [x] **Chunk B — Fourth trash kind** (BE): `PurgeCounts`/`TrashRead`/`EmptyTrashResult`/
      `TrashCountResult` and `count_trash`/`empty_trash` gain `training_examples`; `/trash`,
      `/trash/count`, and empty-trash thread it through. Empty-trash purges any *trashed* examples.
- [x] **Chunk C — Move-to-trash on /training** (FE): `deleteTrainingExample`/`restore`/`purge` API
      calls; `useTraining.deleteExample` drops the row locally, refreshes corpus stats (goal meter
      falls) and the sidebar trash badge; per-example trash button (light confirm — reversible).
- [x] **Chunk D — Training section on /trash** (FE): types + `TrashCountContext` + `useTrash`
      (`training` kind, restore/purge maps, `restoreTrainingById`) + a Training examples section with
      Restore / Delete-forever, type filter, nav count, empty-trash all updated.
- [x] **Chunk E — Tests + docs**: new route tests (delete drops from list+stats, restore, purge
      409/404, trash round-trip) + updated trash response-shape tests; full pytest green (178+). No
      Alembic. README schema-philosophy + roadmap updated.

---

## Sprint 9k — Today / Daily Schedule
> Goal: turn accepted, not-done tasks into a useful plan for the day without AI involvement.

- [x] `backend/app/services/today.py` — pure Python scheduler ranks tasks by in-progress/open,
      due urgency, priority, and shorter estimates as a tie-breaker.
- [x] `GET /api/today` — validates date, start time, and available minutes at the API boundary.
- [x] `/today` frontend page — timeline, overflow, blocked-task, and empty states.
- [x] Dashboard "Today's Tasks / Due Soon" tile links into the schedule view; `/today` is not
      added to the sidebar.
- [x] Blocked tasks are surfaced separately and never scheduled.
- [x] Missing estimates default to 30 minutes and are labelled as assumed.
- [x] Backend today/service route tests and a TodayPage frontend test shipped.
- [x] No model call, schema change, Alembic migration, or new dependency.

---

## Sprint 9L — Recurring Task Stubs
> Goal: add optional recurrence while keeping all control flow in the Python service layer.

- [x] `tasks.repeat_interval` JSON column + `tasks.recurrence_id` series chain added by
      Alembic migration `20260620_b9f8eaebb17c`.
- [x] `RepeatInterval` Pydantic schema validates `{unit: day|week|month, every: 1-12}`;
      recurrence requires a `due_date` and returns 422 otherwise.
- [x] `PATCH /api/tasks/{id}` accepts `repeat_interval`, `skip_recurrence`, and
      `edit_scope`.
- [x] Completing a recurring task creates the next top-level accepted/open occurrence with
      the due date advanced from the current occurrence, including day-clamped month math
      (`Jan 31 + 1 month -> Feb 28`).
- [x] `skip_recurrence=true` marks the current occurrence done without creating the next one.
- [x] `edit_scope="future"` forward-patches same-series rows due on or after the current
      task, leaving already-done/past occurrences alone.
- [x] Frontend shipped `RepeatIntervalInput`, `EditScopeModal`, a task-detail skip button,
      recurrence-aware save wiring, and a TaskCard repeat badge.
- [x] Backend recurrence tests and frontend recurrence tests shipped.
- [x] Pure Python service layer only: no AI, calendar sync, model call, or new dependency.

---

## Sprint 9m — Command-Bar Slash Actions (`/new`, `/done`)
> Goal: finish the deliberate seam in the generic `CommandSearch` topbar — a leading `/` switches the bar from search into an action — without opening a new concept.

- [x] `frontend/src/features/search/parseCommand.ts` — pure parser maps raw input to a
      discriminated command: `/new <text>`, `/done <query>`, plain `search`, or a disabled
      `hint` for a bare `/` or an argument-less verb. Case-insensitive verb, trimmed arg,
      whitespace-separated (so `/newfoo` is an unknown verb → search).
- [x] `/new <text>` captures via `createInbox`, runs `processInbox`, then navigates to
      `/inbox/:id` (the existing note-review route). An in-flight lock blocks a
      double-submit; server-side input-hash dedupe makes repeats idempotent.
- [x] `/done <query>` reuses `GET /api/search` (debounced `useSearch`), lists only matching
      tasks, and completes the chosen one via `POST /api/tasks/{id}/done` — the dedicated
      endpoint, so recurrence's next-occurrence creation is preserved.
- [x] `SearchResultItem` gained `review_status`/`workflow_status` (serialized off existing
      `Task` columns — null for projects/inbox, **no migration**); `/done` filters to
      `accepted` + not-`done` tasks. Mirrored in `frontend/src/types/search.ts`.
- [x] Unified `ActionRow` model in `CommandSearch`: search hits, the `/new` confirm row, and
      `/done` matches are one keyboard-navigable list, each carrying its own `onSelect`.
- [x] Discoverability: updated placeholder + a one-line hint row (`/new` · `/done`) for a
      bare `/`. Toasts on success/failure via the existing `useToast`.
- [x] `parseCommand` unit tests + extended `CommandSearch`/search tests (search-service test
      asserts the two new task fields, null for other kinds). `pytest` + `npm run test` green.
- [x] No AI surface, no model call, no schema change, no Alembic, no new dependency.

---

## Sprint 9n — Today / Daily Schedule Actionability
> Goal: turn the read-only `/today` view into the place you run your day from — act on rows in place, and make blocked rows self-explanatory.

### Slice 1 — Today quick actions (frontend-only)
- [x] `Start` and `Mark done` actions on every scheduled and overflow row
      (`TodayRowActions` in `frontend/src/features/today/TodayPage.tsx`), styled with the
      shared `.task-action` button.
- [x] Mark done goes through the dedicated `POST /api/tasks/{id}/done` (`markTaskDone`), so
      recurrence's next-occurrence creation (Sprint 9L) is preserved — never a raw
      `PATCH workflow_status=done`.
- [x] Start sends `PATCH /api/tasks/{id}` `{ workflow_status: "in_progress" }`
      (`updateTask`); in-progress rows hide Start (they're already started) but still offer
      Mark done.
- [x] Both actions refetch the plan on success via the already-exposed
      `useTodayPlan().refetch()`, so the row re-ranks (Start pulls it up the timeline) or
      drops out (done). Per-row pending state disables the buttons mid-flight so a
      double-click can't double-fire; errors surface through the existing `useToast`.
- [x] No backend change — all three endpoints already existed.

### Slice 2 — Blocked dependency clarity (backend serialization + frontend)
- [x] `app/schemas/today.py` — new `BlockingTask` (`task_id`, `title`, `workflow_status`);
      `BlockedTask.blocking_task_ids: list[int]` replaced by `blocking_tasks:
      list[BlockingTask]`. Serialization-shape change only — **no DB column, no migration**.
- [x] `app/services/today.py` — `_unfinished_dependency_ids` → `_unfinished_dependencies`,
      returning `BlockingTask`s from the same `get_task` loop it already ran (no new query).
- [x] `frontend/src/types/today.ts` + `TodayPage.tsx` `BlockedRow` — each blocker renders as
      its title + a workflow-status pill linking to `/tasks/:id`, replacing the bare `#id`
      list; the "Waiting on N unfinished dependencies" lead-in is kept.
- [x] Blocked-row blocker actions were left out of scope (the plan gated them behind "only if
      free"); slice stays a clarity change, not a third action surface.
- [x] Backend `test_today.py` + `test_routes_today.py` assert the enriched blocked payload;
      `TodayPage.test.tsx` covers Start/Mark-done clicks (+ refetch) and the richer blocked
      row. `pytest` (221) + the `TodayPage` suite green; `tsc`/eslint/`mypy --strict` clean.
- [x] No model call, no eval change, no schema/migration, no Alembic, no new dependency.

## Sprint 9o — Command Bar Completion (`Cmd/Ctrl+K` + search relevance)
> Goal: finish the two stubbed CommandSearch behaviours — make the advertised `Cmd K` hint real, and rank global search by relevance instead of newest-first.

### Slice 1 — Global `Cmd/Ctrl+K` focus shortcut (frontend-only)
- [x] `frontend/src/features/search/CommandSearch.tsx` — `inputRef` on the `<input>` and a
      `window` `keydown` `useEffect` matching `(metaKey || ctrlKey) && key === 'k'`;
      `preventDefault()` (so the browser doesn't grab Ctrl+K), then `focus()` + `select()` +
      `setOpen(true)`. Listener cleaned up on unmount.
- [x] Escape behaviour unchanged — the existing `onKeyDown` already blurs the input; the
      shortcut just re-focuses, no "previously focused element" tracking (out of scope).
- [x] `CommandSearch.test.tsx` — Cmd+K focuses + selects and the listbox opens after typing;
      a Ctrl+K variant covers non-mac; a bare `k` keypress is asserted to be a no-op.

### Slice 2 — Search relevance ranking (backend, pure SQL/Python)
- [x] `backend/app/services/search.py` — replaced per-kind `ORDER BY <pk> DESC` with
      SQLAlchemy `case()` relevance ordering. `_text_tier()` helper scores 0=exact
      (`func.lower(col) == func.lower(q)`), 1=prefix, 2=substring on the primary column,
      3=secondary-only, reusing `_escape_like` for the `q%` / `%q%` patterns.
- [x] Tasks order by text tier first, then a separate state tie-breaker
      (`accepted` + not-`done` before done/candidate), then recency. Inbox prefers a
      `summary` hit over a `raw_text`-only hit.
- [x] `SearchResults` payload shape identical; `schemas/search.py` now reuses the model
      task status enums for type alignment. Only ordering within each group differs; the
      frontend renders groups in received order and needs no change for slice 2.
- [x] `backend/tests/test_search.py` — ordering assertions: exact title beats a newer
      description-only match; prefix beats substring; accepted+open beats done at the same
      tier; inbox summary beats raw-text-only. Existing escape/cap tests still pass.
- [x] `pytest` (225) green; `CommandSearch` Vitest suite green; `tsc` clean. No model call,
      no eval change, no schema/migration, no Alembic, no new dependency. (Pre-existing
      `ProjectDetailPage.test.tsx` flake is unrelated — fails identically on a clean tree.)

## Sprint 10a — AI "Break this down" (per-task subtask suggestion)
> Goal: add a second correctable AI surface that feeds the training corpus — a per-task action that suggests subtasks as review-queue candidates, reusing the inbox-extraction pattern end to end.

### Slice 1 — schema, profile, prompt, Pydantic I/O
- [x] `tasks.breakdown_output_json` nullable column + Alembic migration `5b5f79d37b6e`.
      Holds the raw model output on the parent **only between generating subtasks and
      reviewing them**, so the correction (accepted/edited vs original) can be captured to
      `ai_training_examples` at review time (prime directive #4); cleared on review. The
      backlog's "no new schema" hope was not achievable — the original output must survive
      from generate-time to review-time, and a nullable column is the honest carrier.
- [x] `app/ai/schemas.py` — `BreakdownSubtask` / `BreakdownOutput` / `BreakdownInput`,
      mirroring the extraction schemas (`extra="forbid"`; `confidence` has no default per
      the required-nullable model-field rule).
- [x] `break_down_task` profile in `profiles.yaml` (gemma4:e2b, json_schema) +
      `ai/prompts/break_down_task.md` (decompose within scope; atomic/vague guidance).

### Slice 2 — workflow, review capture, routes, evals
- [x] `app/ai/workflows/break_down_task.py` mirrors `extract_tasks.py`: idempotent
      (existing candidate children or a pending `breakdown_output_json` short-circuit the
      model call), gateway call, Pydantic validation, training-failure capture + 422 on
      invalid output, candidate children via existing `create_task(parent_task_id=...)`
      (project inherited from parent).
- [x] `app/services/breakdown.py` — `review_breakdown`: approve flips a candidate child to
      accepted (with edits), dismiss soft-deletes it; once no candidates remain, writes one
      `ai_training_examples` correction row (full input/output/corrected) and clears
      `breakdown_output_json`. `AlreadyReviewedError` when nothing is pending.
- [x] `app/api/routes_tasks.py` — `POST /api/tasks/{id}/break-down` (422 on invalid model
      output) + `POST /api/tasks/{id}/breakdown/review` (409 when nothing pending). New
      `schemas/tasks.py` schemas (`SubtaskEdit` / `SubtaskDecision` / `BreakdownReviewRequest`
      / `BreakdownReviewResult`).
- [x] `ai/evals/breakdown_cases.yaml` + `run_breakdown_evals.py` (exposes `run()`),
      registered in `services/settings.py` `_EVAL_SUITES`. 6/6 on gemma4:e2b (the atomic
      case asserts only the reliable no-fan-out signal — the small model won't set
      `needs_review` on atomic tasks; revisit with the custom model).

### Slice 3 — frontend (TaskDetailPage)
- [x] `api/tasks.ts` — `breakDownTask(id)` + `reviewBreakdown(id, decisions)`; types in
      `types/task.ts`. "Break this down" button in the Subtasks heading; suggested candidates
      render as `TaskCard`s with Approve / Dismiss (per-row in-flight guard), using the
      page's existing save-state/error feedback. `TaskDetailPage.test.tsx` covers the flow.

### Verification
- [x] `alembic upgrade head` clean; `pytest` (236 + new breakdown/route tests) green;
      `mypy --strict` clean on the new/changed modules; `npm run build` clean;
      `TaskDetailPage.test.tsx` 5/5; `run_breakdown_evals` 6/6. No new dependency.
- [x] Follow-up frontend quality cleanup — stale `DashboardPage.test.tsx` /
      `ProjectDetailPage.test.tsx` expectations fixed; provider hooks split out of
      component files for Fast Refresh; effect-driven loading/draft resets refactored
      to satisfy the React 19 hooks lint rules. No schema, backend, or dependency change.

---

## Sprint 10b — Calendar view
> Goal: an internal read-only calendar of tasks by due date, reached from the dashboard — not external Google/iCal sync (that stays on the do-not-build list).

- [x] Read-only month/week calendar of tasks by `due_date` at `/calendar`, backed by
      `GET /api/calendar?start=&end=` and a new `services/calendar.py`.
- [x] Calendar query returns accepted tasks (including done); candidate and deleted tasks
      are excluded. Flat `list[TaskRead]` reusing `_reads_with_blocked`.
- [x] Dashboard "Upcoming Events" tile is now real: soonest-due tasks plus a working
      **View calendar** link. The calendar is reached only via that tile, not the global nav.
- [x] No schema/migration, model call, or new dependency.

---

## Sprint 11 — Kanban board over `workflow_status`
> Goal: a board view over `open` / `in_progress` / `done` reusing existing task cards and endpoints. Frontend-only.

- [x] `?view=board` toggle on `TasksPage` (global `/tasks` and per-project
      `/projects/:id/tasks`); `KanbanBoard` flat-card columns reusing `TaskCard`.
- [x] Native HTML5 drag plus a per-card "Move to" `<select>` for keyboard/a11y. The Done
      column is sourced from the completed archive (`useCompletedTasks`).
- [x] Moves route to the correct endpoint: into Done → recurrence-safe `POST /done`, out of
      Done → `reopen` (→ open, then PATCH if In progress), else `PATCH workflow_status`.
- [x] Refuses moving a derived-`is_blocked` task into In progress/Done (toast).
- [x] No new backend route, schema/migration, model call, or new dependency.

---

## Sprint 12 — Recurring series management
> Goal: view and stop a recurring series from the task detail page, building on the Sprint 9L recurrence stubs.

- [x] `GET /api/tasks/{id}/series` returns every occurrence sharing a `recurrence_id`
      (including soft-deleted skipped rows, oldest first).
- [x] `POST /api/tasks/{id}/stop-recurrence` clears `repeat_interval` while keeping the
      chain id intact.
- [x] Lazy-loaded `RecurrenceSeries` timeline + confirm-gated Stop recurrence on
      `TaskDetailPage`. Future edits already shipped via `edit_scope` (Sprint 9L).
- [x] No migration, model call, or new dependency.

---

## Sprint 13 — AI Subsystem Quality

Three cohesive AI-workflow polish items; no schema/migration, no Alembic, no model
call, no new dependency.

### Eval regression warning (frontend-only)
- [x] `SettingsPage.tsx` `EvalTrend` — compares the latest run against the previous
      one for the same suite (`runs[0]` vs `runs[1]`, already fetched newest-first) and
      renders a red `status-pill tone-red` "▼ regressed" badge with a "down from N%"
      title when the pass rate dropped. No backend change.

### Prompt snapshot on save (backend)
- [x] `services/settings.py` `put_prompt` — `_snapshot_prompt` copies the current
      on-disk prompt to `ai/prompts/.history/<name>.<UTC-timestamp>.md` (microsecond
      precision so same-second saves don't collide) before overwriting; logs
      `prompt_snapshot_saved`. `.history/` is gitignored and not matched by
      `list_prompts`' top-level `*.md` glob. No new route.

### Training corpus QA filters (backend + frontend)
- [x] `services/training_data.py` — `list_examples` replaced the `accepted` bool param
      with a `status` Literal (corrected / accepted / failure) mirroring the frontend
      `statusOf` taxonomy + added a `model_profile` filter; `example_stats` now also
      returns the distinct sorted `profiles` list.
- [x] `routes_training.py` + `schemas/training.py` — `status`/`model_profile` query
      params (validated Literal); `TrainingStatsRead.profiles`.
- [x] `types/training.ts` / `api/training.ts` / `TrainingPage.tsx` — 3-way Status
      dropdown + new Profile dropdown (from `stats.profiles`); `filtered`/`clearFilters`
      updated.

### Cleanup
- [x] Retired dead backlog item "Surface AI inbox summary as note title" — already
      live (`InboxPage` renders `item.summary ?? item.raw_text`).

### Verification
- [x] `pytest` green (new training filter/stats + prompt-snapshot tests);
      `ruff`/`mypy --strict` clean on changed backend modules; `tsc --noEmit` clean.

---

## Sprint 14 — Security Posture Hardening

Focused security backlog slice; no schema/migration, no Alembic, no model call,
no provider change, and no new dependency.

- [x] Web + Discord inbox capture now use `InboxRawText`, an 8,000-character
      stripped/nonblank Pydantic type. Oversized `POST /api/inbox` and
      `POST /api/discord/inbox` payloads fail validation before DB writes or model
      calls.
- [x] Discord `/inbox` success and error followups pass
      `AllowedMentions.none()`, so echoed user/model text cannot ping roles or
      users.
- [x] README documents the intentional single-user/trusted-LAN posture:
      `API_HOST=127.0.0.1` is safest/default; `API_HOST=0.0.0.0` exposes normal
      app read/write APIs to trusted LAN clients; Settings writes remain
      loopback-only; Discord routes rely on `BACKEND_SHARED_SECRET`; this is not
      multi-user auth.
- [x] `require_local_settings_write` documents its direct-bind assumption and the
      need for explicit trusted-proxy handling before reverse-proxy use.
- [x] Added backend regression tests for exact-limit and over-limit web/Discord
      inbox capture. Per user request, tests were not run locally.
- [x] Credential rotation was intentionally left untouched.

---

## Sprint 15 — UX Foundation

Frontend-only UX foundation slice; no backend route, schema/migration, Alembic,
model call, provider change, or new dependency.

- [x] Frontend routing now uses React Router data routing (`createBrowserRouter` +
      `RouterProvider`) with `AppShell` as the root layout. All existing routes were
      preserved.
- [x] Settings keeps the existing browser close/reload `beforeunload` guard and now
      blocks in-app route changes while profile/prompt edits are dirty. The blocker
      uses the existing modal style with `Stay` and `Leave without saving`.
- [x] `AppShell` no longer shows fake/static chrome: the focus-session claim,
      disabled notification/search/customize buttons, and fake sync timestamp were
      replaced with honest local workspace/status copy.
- [x] `TasksPage` syncs filters, sort, board/list view, and `new=1` task-create deep
      links into canonical query params. Browser back/forward restores task view
      state from the URL.
- [x] Added/updated frontend tests for route rendering inside `AppShell`, Settings
      route blocking, shell truthfulness, and task URL sync/history behavior. Per
      user request, tests were not run locally.

---

## Sprint 16 — Blocking-Task Emphasis

Dependency-attention slice; no schema/migration, Alembic, model call, provider
change, eval change, prompt change, AI training-data change, or new dependency.

- [x] `TaskRead` gained derived `is_blocking` and `blocked_task_count` fields.
- [x] `services/task_dependencies.py` now computes top-level blockers from active,
      accepted, unfinished dependency edges. A chain such as `A depends on B
      depends on C` marks only `C` as blocking and counts both downstream tasks.
- [x] Task serialization populates `is_blocked`, `is_blocking`,
      `blocked_task_count`, and roll-ups together for list/detail/calendar-style
      consumers.
- [x] Dashboard dependency emphasis now surfaces root blockers: the red card is
      `Blocking Work`, links to `/tasks?status=blocking`, and lists top blockers
      with downstream counts. Merely blocked downstream tasks are secondary.
- [x] `TaskCard`, `TaskDetailPage`, `TasksPage`, and shared project-status logic
      now reserve red for `Blocking`; downstream `Blocked` tasks use neutral
      waiting treatment. `TasksPage` gained the `Blocking` status filter.
- [x] Blocking task detail views now show a read-only `Blocking` section listing
      direct dependent tasks via `GET /api/tasks/{id}/dependents`.
- [x] Added backend and frontend regression tests for the new derived behavior.
      Per user request, tests were not run locally.

---

## Sprint 17 — Static read-only project Gantt (custom renderer)
> Goal: slice 1 of the re-decomposed planning view — a read-only per-project timeline rendered with a custom CSS/SVG Gantt (no third-party library; the frappe-gantt attempt was abandoned as the wrong shape for React).

- [x] Per-project Timeline tab + `/projects/:id/timeline` route; shared `ProjectTabs`
      mounted on all three project routes.
- [x] `GanttChart` custom CSS-grid renderer: day axis, weekend/today shading, today
      marker, and absolutely-positioned bars from `scheduled_start` + `estimated_minutes`
      via `ganttModel.ts`.
- [x] Bars carry status/blocked/blocking colors, conflict outlines, and a per-bar
      due-date marker; loading/empty handled via `AsyncState`; a display-only unscheduled
      bucket lists tasks with no `scheduled_start`.
- [x] Added the `scheduled_start` column + PATCH plumbing (Alembic migration) that later
      drag-to-reschedule slices build on.
- [x] Read-only — no drag yet (that is slice 2, the current focus; see `CURRENT.md`).
- [x] Added a happy-path test for the renderer/model.

---

## Sprint 18 — Gantt interactivity: drag-to-reschedule + bar-resize
> Goal: slices 2 & 3 of the planning view — make the Gantt bars editable. FE-only;
> the backend already accepts `scheduled_start` and `estimated_minutes` on
> `PATCH /api/tasks/{id}`, so no schema/migration/model/eval/prompt change.

- [x] **Slice 2 — drag-to-reschedule:** horizontal bar drag sets `scheduled_start`.
      New `useDragReschedule` gesture hook (measures the flexing day-column width
      from the DOM, converts the pointer delta to whole days) + `useProjectGantt.reschedule`
      (optimistic move, revert-on-error, toast, then refetch to reconcile derived
      conflict/blocked flags).
- [x] **Slice 3 — bar-resize to edit estimate:** a right-edge `.gantt-resize-handle`
      drags to set `estimated_minutes` (one day-column = 480 min, clamped to a 1-day
      floor). New `useBarResize` gesture hook (mirrors `useDragReschedule`) + a live
      span preview + `useProjectGantt.resize`. Parent bars expose no handle — their
      estimate is a server rollup of subtasks and is not directly settable, so a
      handle there could only no-op; a tooltip explains the rollup instead.
- [x] **Bugfix:** bars are `<Link>` anchors, which are natively draggable — that
      hijacks the pointer stream so the window `pointermove`/`pointerup` listeners
      never fired and *both* gestures were dead in the real browser (unit tests
      passed because jsdom has no native drag). Fixed with `draggable={false}` on the
      bar `<Link>`.
- [x] **Tooling:** added Playwright (frontend devDependency) + a `verifier-browser`
      skill so the drag gestures — which jsdom cannot exercise — are verifiable in a
      real headless browser. Verified slice 3 end-to-end this way (leaf resize
      persisted, parent-override prompt fired, move-drag restored).
- [x] Extended the `buildGanttModel` unit test for the new bar fields
      (`hasSubtasks`, `estimatedMinutes`).
