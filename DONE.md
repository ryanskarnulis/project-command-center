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

## Round 5 — Cleaning & hardening (comprehensive review)
> A full-codebase review (architecture, runtime bugs, data model, API, frontend, security,
> performance, tests, maintainability) run 2026-07-01. The three top findings were
> **reproduced against the live service layer**; the full quality gate passed at review
> time (315 backend + 213 frontend tests, ruff, `mypy --strict` on `app/`, eslint, build).
> **Round closed 2026-07-01**: all confirmed bugs, hardening follow-ups, and
> boundaries/tests/docs items are done (gate now 325 backend + 228 frontend tests, with
> `mypy --strict` covering `tests/` too). The Performance section and the improvement-ideas
> notes were left in `TODO.md` by design — see the "Deferred hardening notes" section there
> *(section later removed in the #6 pivot)*.
> _Severity: (high) user-facing breakage · (med) bug or confusing state · (low) polish/docs._

### Confirmed bugs (reproduced — fixed)
- [x] **(high) Skip on a recurring checklist orphans its subtasks** — `skip_occurrence`
      (`services/tasks.py`) soft-deleted only the occurrence row, not its subtree; the old
      occurrence's subtasks stayed active pointing at a trashed parent, and the frontend's
      `buildTaskTree` promoted orphans to roots, so every skip leaked a stale subtree copy
      into the task list. Fixed: cascade via `soft_delete_task`. Added a skip-with-subtasks
      case to `test_recurrence.py` (only a leaf skip was covered before).
- [x] **(med) `edit_scope="future"` forward-patch bypassed all write guards** — the bulk
      UPDATE in `update_task` propagated every non-excluded field, including
      `parent_task_id`, onto future series rows without the cycle / derived-status /
      blocked checks; a crafted PATCH could make an occurrence its own parent (API-only —
      the UI never scopes parent edits). Fixed: added structural fields (`parent_task_id`,
      `project_id`, `review_status`) to `_FORWARD_PATCH_EXCLUDE` (the acted-on row still
      takes the guarded edit; only forward propagation is skipped) + regression test.
- [x] **(med) Fully-completed checklist parents never left the open list** — a parent's
      rolled-up status read `done` but the stored column stayed `open`, so
      `list_tasks(exclude_done=True)` and the dashboard open counts kept it in open
      lists/counts forever (and hid it from the completed view too). Fixed by making
      `list_tasks` status filtering **rollup-aware** (effective status, not the stored
      column) for both the `exclude_done` and explicit `workflow_status` paths, plus the
      dashboard counts — consistent with the "derived, never stored" model, so a
      fully-done checklist parent leaves the open list and lands in the completed archive.
      `dashboard.get_overview` computes the open set once to stay within its query budget.
      Also fixed the inverse (a done leaf re-opened by adding a child).
- [x] **(med) Breakdown training rows absorbed pre-existing manual subtasks** —
      `review_breakdown` (`services/breakdown.py`) built `corrected_output_json` from
      *all* accepted children, so subtasks created by hand before "break this down" got
      recorded as output the model "should have" produced. Fixed: corrected output (and
      the `accepted` flag) is now scoped to the breakdown's own approved candidates (+
      edits) so the fine-tuning corpus stays honest (prime directive #4).

### Hardening & correctness follow-ups
- [x] **(med) Loopback-gate the destructive purge routes** — the settings guard moved to
      `api/guards.py` as `require_local_write` (generalized 403 message) and now also gates
      `DELETE /api/trash` and all four per-item `/purge` routes. Reversible operations
      (trash/restore) stay open to LAN clients. LAN-client 403 regression tests added to
      `test_routes_trash.py`.
- [x] **(med) Recurrence × subtask × dependency interaction test pass** — skip/restore of
      checklist occurrences, forward-patch propagation/exclusion, and rollup-vs-stored
      list/count boundaries were covered with the round-5 bug fixes; added the missing
      un-skip-with-dependencies cases (edges on the skipped row are purged with it — no
      dangling edge, no phantom block on either the retargeted occurrence or a dependent).
- [x] **(low) Unify naive/aware datetimes** — standardized on aware UTC: `TimestampMixin`
      now uses a Python-side `utcnow` default/onupdate (DDL unchanged, so no migration),
      and read schemas use a `UTCDateTime` type that stamps legacy naive rows as UTC at
      the serialization boundary — JSON always carries an offset, so JS parses it as UTC
      and renders correct local time.
- [x] **(low) SQLite WAL + busy_timeout pragmas** — added `journal_mode=WAL` and
      `busy_timeout=5000` next to the FK pragma in `db/session.py`.
- [x] **(low) `apiClient` timeout/abort** — every request now aborts via
      `AbortSignal.timeout` (30s default; 180s for the model-backed break-down, inbox
      process, summary, and eval-run calls) and surfaces a readable `ApiTimeoutError`.
      A caller-supplied signal still wins.
- [x] **(low) TaskDetailPage 404 detection** — now `e instanceof ApiError && e.status
      === 404`.

### Boundaries, tests, docs
- [x] **(low) Move task read-model assembly to a shared module** — extracted to
      `api/task_reads.py` (`read_with_blocked` / `reads_with_blocked`, now public);
      `routes_tasks` and `routes_calendar` both import it.
- [x] **(low) Dedupe the purge-endpoint 409/404 branch** — extracted
      `trashed_row_or_error` into `api/guards.py` (lazy active-lookup, per-kind
      messages preserved); all four purge routes use it.
- [x] **(low) Split `services/tasks.py`** — extracted `task_recurrence.py` (next-
      occurrence math, checklist cloning, skip/series/stop) and `task_trash.py`
      (trash list/restore/purge); `tasks.py` is core CRUD + guards + rollups
      (894 → 557 lines). The only remaining inversion is the completion-spawn hook,
      a documented local import in `update_task`/`mark_done`.
- [x] **(low) Split `TaskDetailPage.tsx`** — extracted `BreakdownReview` and
      `CandidateDecisionBar`, and the page now reuses the shared `SubtaskComposer`
      (its `onMoreOptions` became optional) instead of a duplicated inline form
      (842 → 706 lines).
- [x] **(low) mypy on `tests/`** — all 45 errors fixed (typed helpers, narrowed
      nullable columns, imports from source modules instead of re-exports);
      `test.sh` now runs `mypy app tests`.
- [x] **(low) Frontend tests for `features/training/` and `ActivityFeed`** — added
      `TrainingPage.test.tsx` (stats/status pills, filters, pagination, trash flow),
      `diff.test.ts`, and `ActivityFeed.test.tsx` (lazy fetch on expand, empty/error
      states, refreshKey) — 15 new tests.
- [x] **(low) README repo-layout drift** — `llamacpp.py` and `docker-compose.yml` are
      now annotated as planned/deferred; `openai_compatible.py` dropped from the layout.
- [x] **(low) `break_down_task` builds `input_text` twice** — deduped.

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

---

## Sprint 23 — Global cross-project planning surface
> Goal: slice 8 of the planning view — a `/planning` route rendering every project's scheduled work on one shared axis, bars grouped and colored by project. The renderer/model/axis were already project-agnostic, so the work was the multi-project data shape + grouping/coloring + a cross-project cascade fix.

### Backend
- [x] `GET /api/planning/gantt` → `GlobalGantt` schema (tasks across all projects +
      the edges among them, which may cross project boundaries, + a `projects` legend).
- [x] `services/planning.all_gantt_tasks` — accepted, not-done tasks over *all*
      projects (`list_tasks(project_id=None, …)`); reuses `gantt_dependencies`.
- [x] Cross-project cascade fix: new `cascade_from_task` (loads all projects' tasks +
      edges, runs the unchanged pure `compute_shifts`) replaces the project-scoped
      `cascade_downstream`; `routes_tasks.update_task` now fires it, so a dependent in
      another project shifts when its blocker moves.
- [x] Route tests: global endpoint shape, cross-project edges, projects-with-tasks-only
      legend, soft-delete exclusion, and a cross-project PATCH cascade in `test_planning.py`.

### Frontend
- [x] `getGlobalGantt` API + `GlobalGantt`/`GanttProject` types.
- [x] `projectId` carried onto every `GanttBar` + `UnscheduledTask` in `buildGanttModel`.
- [x] `projectColors.ts` (pure, deterministic per-project palette) used for bar accents
      + the page legend.
- [x] `GanttChart` grows an optional `projects` prop: bars render in labeled per-project
      sections (a group-header row before each project's bars) with a left project-color
      accent; omitting the prop keeps the per-project timeline's single flat list.
- [x] `useGlobalGantt` hook (the cross-project twin of `useProjectGantt`) +
      `GlobalPlanningPage` (zoom + legend + `AsyncState`, no what-if) at `/planning`;
      added a "Planning" entry to the sidebar nav.
- [x] Tests: `GlobalPlanningPage.test.tsx` (grouped sections, legend, drag-reschedule) +
      a `ganttModel` projectId test.
- [x] The calendar variant was left to the existing `/calendar` view (out of scope).

---

## Sprint 24 — Drag from the unscheduled bucket
> Goal: slice 9 of the planning view (the last queued slice — the planning-view epic is now **complete**). An unscheduled item drags from the side bucket onto a chart column to schedule it on that column's date.

### Frontend
- [x] `useBucketDrag` hook — mirrors the bar-drag pointer-event lifecycle (the codebase
      opts out of native HTML5 DnD), but where bar-drag computes a *delta* a bucket drop is
      *absolute*: it resolves the landed column on `pointerup` and schedules to that date.
- [x] `columnAtClientX` (pure, unit-tested) — hit-tests the drop x against the rendered
      `.gantt-col-bg` cells and reads the landed column's own `iso`, so no date math runs in
      the frontend (prime directive #1).
- [x] No estimate is written: a `null` estimate already renders a 1-day bar via `spanDays`'s
      floor-of-1, so the default 1-day span is emergent — no backend change.
- [x] Reuses `useProjectGantt.reschedule` (scheduling *is* setting `scheduled_start`):
      optimistic + revert-on-error + toast + refetch; what-if staging works for free via
      `whatIf.stageStart`.
- [x] A floating drag ghost follows the pointer + a drop-target column highlight.
- [x] Tests: `useBucketDrag.test.ts` + TimelinePage bucket-drag tests.
- [x] FE-only — no schema/migration/model/eval/prompt/dependency change.

---

## Planning view (Gantt/calendar) — REMOVED
> The planning-view epic above (Sprints 17–24: static Gantt, drag-reschedule,
> dependency lines, auto-shift, what-if, zoom, global cross-project surface, and
> bucket-drag) was shipped and then removed (commit `04dea44` "removed gantt").
> It didn't earn its complexity. This ledger keeps the build history intact for
> the record; the feature is no longer in the app. Date scheduling and the
> dependency cascade still live in Python (CLAUDE.md prime directive #1).

---

## Cleaning & hardening — manual review (round 2)
> Findings from a full browser-driven QA pass on 2026-06-23. Triaged by severity
> and folded into the post-Gantt-removal cleanup. Frontend-only except where noted.

- [x] **(high) Project "Timeline" tab dead link → app error page** — the Gantt
      removal (`04dea44`) missed the per-project **Timeline** `NavLink` in
      `ProjectTabs.tsx`, which pointed at the deleted `/projects/:id/timeline`
      route and dumped users on React Router's developer error page. Dropped the tab.
- [x] **(med) No app-level error boundary / catch-all route** — `AppRoutes.tsx` had
      no `errorElement` and no `*` route, so any bad URL or thrown route error showed
      the dev page. Added `RouteErrorBoundary` + a friendly `NotFoundPage`
      (`features/errors/`).
- [x] **(med) `formatDuration(0)` rendered "0 weeks"** — `splitDuration` checked
      `minutes % WEEK === 0` first (true for 0). Special-cased 0 → "0m"
      (`utils/duration.ts`).
- [x] **(med) Today empty-state copy contradicted the overflow list** — when 0 tasks
      fit but overflow > 0, `TodayPage` showed "No open tasks…" above a populated
      "Didn't fit" section. Copy now reflects the overflow case.
- [x] **(low) Greedy day-packing second look** — confirmed `_pack` backfills
      (oversized high-rank item goes to overflow, scan continues), so a day only reads
      empty when nothing fits.
- [x] **(low) Stale README Gantt sprint log** — cut the per-sprint prose changelog from
      README (duplicated `DONE.md`); added a "Planning view — REMOVED" note to `DONE.md`.
- [x] **(low) No persistent nav to Today / Calendar / Inbox / Tasks** — added them to the
      `AppShell` sidebar primary nav.
- [x] **(low) Inert placeholder controls** — removed the disabled "Customize Command
      Center" / "Ask AI" dashboard buttons and the placeholder sidebar tools.

---

## Cleaning & hardening — manual review (round 3)
> The deeper pass round-2 deferred: Trash restore/purge round-trips, recurrence series
> actions, alias CRUD, and the project-description save flow. Done 2026-06-24 with code
> review + service-layer scripts + headless-chromium browser drives + the full backend
> suite. Each finding was reproduced, not just read.

- [x] **(med) Recurrence + subtasks silently kills the series.** Resolved via the
      "recurring checklist" approach: completing the last child now spawns a fresh clone
      of the whole subtree as the next occurrence (`_maybe_spawn_recurring_checklist` +
      subtree-aware `_create_next_occurrence` in `services/tasks.py`). The parent stays
      derived/read-only; the series advances from the child-completion path.
- [x] **(med) Restoring a skipped occurrence duplicates the live series.** Resolved via
      "un-skip on restore": `restore_task` is now recurrence-aware — when the restored row
      belongs to a series with a live forward occurrence, it pulls that occurrence's date
      (and its subtree's) back to the restored date via `_reschedule_occurrence`, then
      hard-deletes the restored row. The series resumes at the un-skipped date with exactly
      one live occurrence. Tests in `test_recurrence.py`.
- [x] **(med) Restoring a project gives back an empty project.** Resolved by making
      delete/restore symmetric and project-scoped: deleting a project now cascade-soft-deletes
      its tasks (and subtrees) with it (stamped `tasks.deleted_with_project_id`, migration
      `5be1ff02ca06`); restoring asks whether to bring those tasks back
      (`restore_project(restore_tasks=...)`). `/trash` project cards show `archived_task_count`
      and a confirm-gated "bring back N tasks" restore. Backend + frontend tests.
- [x] **(low/med) Duplicate & case-variant aliases are accepted.** Resolved with a
      normalized dedupe guard backed by the DB: `project_aliases` gains a `normalized_alias`
      column and a partial unique index over active rows (`uq_project_alias_normalized`,
      migration `7ebcc24824c9`). `create_alias` raises `DuplicateAliasError` → 409; the
      frontend pre-disables Add and shows an "already added" hint. Tests in `test_projects.py`.
- [x] **(low) No unsaved-changes guard on ProjectDetailPage / TaskDetailPage.** Resolved by
      adding a `beforeunload` guard to both pages via a shared `useBeforeUnload(dirty)` hook
      (extracted from SettingsPage's inlined effect). Scoped to refresh/close only — in-app
      nav stays covered by save-on-blur. Tests in both detail suites + Settings.
- [x] **(low) Stale "FK enforcement is off on SQLite" comments.** Fixed: the two comments
      (`common.hard_delete`, `tasks._deleted_subtree_depth_first`) now state it accurately —
      FK enforcement is on (`PRAGMA foreign_keys = ON`), but SQLite FKs don't auto-cascade.
- [x] **(low) `POST /api/tasks` silently ignores a supplied `project_id`.** Resolved by
      honoring it: `TaskCreate` gains a `project_id: int | None` field and `create_unscoped_task`
      passes it through, validating a non-null value with `_ensure_project` (404 on a bad id).
      The project-scoped route keeps the path id authoritative. Tests in `test_tasks.py`.

**Verified clean (no action):** recurrence detail UI (repeat badge, skip-with-confirm, lazy
series timeline, `EditScopeModal`); `stop-recurrence`/skip-non-recurring → 422; month-clamp
math; Trash purge round-trips (guards, edge cleanup, idempotency, confirm-gated buttons);
alias add/remove and description save-on-blur persistence.

---

## Cleaning & hardening — manual review (round 4)
> Code-read pass over update/validation, inbox-review, and service-boundary seams. Findings reproduced 2026-06-25.

- [x] **(med) `TaskUpdate` lets non-nullable fields be cleared to `null`.** Distinguished
      *optional-because-omitted* from *nullable-because-clearing-is-allowed* via a
      `model_validator` keyed on `model_fields_set`. `title`, `priority`, `review_status`,
      `workflow_status` now reject explicit `null` with 422; `description`, `due_date`,
      `assignee_hint`, `parent_task_id`, `estimated_minutes`, `repeat_interval` may still be
      nulled.
- [x] **(med) Explicit `project_id: null` does not actually un-file an accepted task.**
      Decision: keep the "global tasks are always filed" model. Fixed the misleading route
      comment and any UI language so code, comment, and UI agree.
- [x] **(med) `review_inbox` can finalize a partial batch.** Added a guard before setting
      `reviewed_at`: the decision `task_id` set must equal the live-candidate id set exactly
      (no missing, no duplicate) — else 422. Tests added for partial/duplicate cases.
- [x] **(low/med) `services/tasks.py` raises HTTP errors from domain code.** Added
      `RecurrenceRequiresDueDateError(ValueError)`, raised from the service, mapped to 422 in
      `routes_tasks.py` alongside `TaskCycleError` / `DerivedStatusError`.
- [x] **(high/docs) `CURRENT.md` contradicts the README's removed-Gantt direction.** Rewrote
      `CURRENT.md` to drop the stale phases/Gantt framing and align with the agent/task
      orchestration direction.
- [x] **(low, refactor) `TasksPage.tsx` god component split.** Extracted into
      `taskFilters.ts` (pure helpers), `useTaskUrlState` (URL-backed state), `TaskFilters`,
      `TaskListView`, `TaskBoardView`, and `SubtaskComposer`. `TasksPage` reduced from ~865 to
      ~198 lines; zero behavior change, existing integration tests pass unchanged.

---

## UI/UX revamp — in-place editing (Slices 1–3)
> Committed 2026-07-01, shipped as "Fable frontend revamp 1/2/3" (commits `58142bf`,
> `77c310e`, `bfc57c1`). Frontend-only: no schema changes, no new AI calls — existing
> endpoints throughout. Full slice detail (chip inventory, token grammar, per-candidate
> capture wiring) is preserved in git history on `CURRENT.md` as of those commits.

- [x] **Slice 1 — Peek panel + editable metadata chips.** Task cards open a right-side
      slide-over panel instead of navigating away (`?task=<id>` on the host page,
      `/tasks/:id` redirects); all 8 fields (status, priority, due, estimate, project,
      assignee, repeat, parent task) became click-to-edit chips (`features/tasks/chips/`),
      replacing the "Task Fields" form column as the one editing grammar. Chips are
      controlled value/onChange components with no `Task` dependency, reused by slices 2–3.
- [x] **Slice 2 — Quick-add bar (token parsing, no modal).** A permanent one-line input
      atop Tasks list/board and project task pages parses `!priority #project ~estimate
      @assignee` tokens deterministically in TS (`features/tasks/quickadd/`), with a chip
      preview and "More options" escape hatch into the full modal (kept for edit/fallback,
      no longer the default creation path).
- [x] **Slice 3 — Inline inbox triage.** Candidate cards on note-review are edited in
      place (title input + slice-1 chips, including description and assignee) instead of
      detouring through the task detail page; approve/dismiss auto-advances to the next
      candidate; correction capture to `ai_training_examples` is preserved unchanged.

## UI/UX revamp companion — card-level quick actions
> Frontend-only. Shipped 2026-07-03 alongside the slices 1–3 in-place editing epic.

- [x] **One-click complete circle on every task card.** `TaskCard` gains an
      always-visible leading circle (Todoist-style) via an `onComplete` prop —
      no more hover-only "Mark done". Wired in the task list (replacing the
      hover check action, same recurrence-safe done endpoint), the kanban board
      (`move(task, 'done')`, guards intact), and ProjectDetailPage incl.
      `SubtaskGroup`. Disabled with a title hint when status rolls up from
      subtasks or the task is blocked; hidden on done cards.
- [x] **Drag card → sidebar project to file it.** The sidebar now lists
      projects under the Projects nav entry (`.shell-nav-projects`, refetched
      per route change); each row is a nav link and a drop target. `TaskCard`
      is draggable, carrying its id as `application/x-pcc-task` (plus
      `text/plain` so kanban column drops keep working); dropping PATCHes
      `project_id` with a "Task filed to X" toast.
- [x] **Cross-page refresh seam.** New `TaskRefreshProvider` /
      `useTaskRefresh` version context: the sidebar drop bumps it and
      `useTasks` + ProjectDetailPage's task loaders refetch off it, so the
      page under the drop updates in place. Vitest coverage in
      `TaskCard.test.tsx` (circle states, drag payload); drag + complete
      verified end-to-end in headless chromium against the live API.

---

## UI/UX revamp companion — inline pill editing on every task card
> Frontend-only, shipped 2026-07-03. Extends the slice-1 chip components (peek panel)
> onto `TaskCard` itself, so status/priority/due/estimate can be edited without opening
> the panel — and removes the kanban board's now-redundant "Move to" dropdown.

- [x] **`TaskCard` gained optional `onUpdate` / `onSetStatus` props.** When passed, the
      status/priority/due-date/estimate pills render as the existing `StatusChip` /
      `PriorityChip` / `DueDateChip` / `EstimateChip` popovers instead of static badges;
      omitting the props (trash, breakdown-review cards) keeps the old read-only badges.
      Chip clicks swallow the card's `<Link>` navigation (with a `requestSubmit()`
      re-trigger for the chip editors that submit via a form), and dragging to select text
      inside an open popover no longer starts a card drag.
- [x] **Kanban board dropdown removed.** `KanbanBoard`'s per-card "Move to column" `<select>`
      is gone; the status chip now routes moves through the same `move()` guards (blocked /
      rolled-up-from-subtasks rules intact). Dead `.kanban-move` CSS removed.
- [x] Wired through `TaskBoardView`, `TaskListView` (active + completed cards),
      `TasksPage`, `ProjectDetailPage`, and `SubtaskGroup`. Done-column edits on the board
      also refresh the completed archive. Inline edits on a recurring task apply to the
      single occurrence only (no scope prompt from the card — that stays on the panel).
- [x] Rewrote the 4 `KanbanBoard` dropdown-driven tests to drive the status chip instead;
      all 130 tasks-feature Vitest cases pass, `tsc --noEmit` clean. Verified live in
      headless chromium: zero dropdowns on the board, chip popover opens without
      navigating, a priority change round-tripped through the API (then reset).

---

## Today page revamp — scheduling, layout, and controls
> Shipped 2026-07-03. One migration (`deferred_until` on `tasks`) plus scheduler,
> schema, and frontend changes across `services/today.py`, `schemas/today.py`, and the
> `features/today/` page.

- [x] **Oversized-parent subtask fill-in.** When a parent task doesn't fit the remaining
      capacity, `_pack` (`services/today.py`) now tries its open accepted subtasks in the
      parent's rank slot before overflowing it; each subtask that fits is scheduled as its
      own timeline block (`parent_task_id`/`parent_title` on `ScheduledBlock`, reason
      prefixed `"part of <parent>"`), and the parent's overflow row carries
      `scheduled_subtask_count`.
- [x] **Defer-to-tomorrow.** New nullable `deferred_until: date` column on `Task`
      (Alembic migration, applied) — a day-plan snooze the scheduler filters on
      (`_is_deferred`), settable/clearable via the existing `PATCH /api/tasks/{id}` and
      excluded from recurrence forward-patching like `due_date`. Every timeline/overflow
      row on the Today page got a **Defer** action that PATCHes `deferred_until` to the
      day after the plan's date.
- [x] **Less scrolling.** "Didn't fit" and "Blocked" render collapsed by default (header +
      count, expand on click) so the timeline owns the page by default.
- [x] **Start time defaults to now** (rounded up to the next 5 min) on every visit;
      capacity presets became 30m/1h/2h/4h/6h/8h plus an **"Until end of day"** mode
      (editable end-of-day time, default 17:00) that computes capacity from start time.
      Capacity mode/minutes/end-of-day persist in `localStorage`; start time intentionally
      does not.
- [x] **Now marker + elapsed dimming.** A "NOW · HH:MM" divider is inserted into the
      timeline (only when viewing today), refreshed every 60s; blocks that already ended
      get a dimmed `.today-block-past` style.
- [x] Backend: 2 new pytest cases (`test_today.py`) for subtask fill-in and deferral
      filtering; full suite 327 passed. Frontend: 4 new/updated Vitest cases in
      `TodayPage.test.tsx` (11 total); `tsc --noEmit` clean. Verified live in headless
      chromium against the running app — now marker/dimming, subtask fill-in label,
      collapsed→expand, "Until end of day" capacity, and a real defer that wrote
      `deferred_until` to the DB and dropped the row (test tasks cleaned up after).

---

## Deployable app — Discord follow-ups, improvement sweep, docker-compose, litestream
> Shipped 2026-07-03 (commits 26–28). Four slices; full detail lived in `CURRENT.md`
> before archival.

- [x] **Slice 1 — Discord `/tasks` + `/done`.** `GET /api/discord/tasks` (open tasks,
      optional `?project=` name/alias filter) and `GET /api/discord/tasks/search?q=`
      (ranked fuzzy title match over open tasks), shared-secret guarded; `/tasks [project]`
      numbered-list bot command and `/done <search>` (exactly-one → recurrence-preserving
      `POST /api/tasks/{id}/done`, multiple → disambiguation, zero → no-match; no writes on
      ambiguity). 8 new tests; README Discord section updated.
- [x] **Slice 2 — Remaining round-5 improvement ideas.** Next-occurrence date on the repeat
      badge (exposed off `services/task_recurrence.py` on the task read payload), skip-an-
      occurrence from the task card menu / Today / series timeline, bulk select + multi-
      restore/purge on `/trash`, and alias-match visibility on triaged inbox notes. Vitest +
      happy-path pytest for the payload field.
- [x] **Slice 3 — docker-compose deployment.** `backend/Dockerfile` (alembic upgrade then
      single-worker uvicorn), `frontend/Dockerfile` (vite build + nginx `/api` reverse proxy,
      SPA fallback, 200s AI-route timeout), `docker-compose.yml` (backend/frontend + optional
      discord-bot profile, `/health` healthcheck, `./data` volume, host-gateway Ollama). The
      settings-write guard was reworked for the NAT'd proxy: `app/api/request_ip.py`
      (`is_trusted_proxy`, `proxy_is_host_only`, spoof-resistant rightmost-XFF
      `resolve_client_ip`) trusts nginx-forwarded writes only while the dashboard is bound
      host-only; `FRONTEND_BIND=0.0.0.0` auto-re-guards writes to 403. Verified end-to-end in
      both modes; two real deploy bugs found and fixed (empty `DISCORD_GUILD_ID=` startup
      crash → `env_ignore_empty=True`; no `curl` in the slim image → Python admin snippet).
      README "Deploy with Docker" section added.
- [x] **Slice 4 — litestream continuous replication.** Default-on `litestream/litestream:0.3`
      sidecar sharing the `./data` mount, `command: replicate` against `litestream.yml` (file
      replica at `data/replica/`; WAL already met — app runs SQLite in WAL mode). S3 target
      left commented with `LITESTREAM_S3_*` stubs; `.gitignore` excludes `data/replica/` +
      the litestream shadow dir. `scripts/backup_db.sh` kept as the manual snapshot path.
      **Restore drill run live:** created a project *after* the initial snapshot, then
      `litestream restore`d the file replica to a scratch path — the post-snapshot project
      was present and all ten tables' row counts matched the live DB, proving the WAL stream
      round-trips. README backups section updated.

---

## Night-silk retheme — The Web design language (spiderweb-restyle phase 3)

- [x] **Dark-only silk theme.** `styles/silk.css` added as a verbatim copy of
      `gateway/theme/silk.css` (canonical; re-copy to fix drift), loaded first in the
      barrel. `tokens.css` remaps every PCC semantic token onto the silk primitives —
      feature partials and components needed zero color changes (the phase-2 token
      consolidation paid off). Old light palette lives in git history. `color-scheme:
      dark`, night-vignette body background, glow hover on buttons, glow focus rings,
      the launcher's corner-web SVG faintly behind the dashboard only.
- [x] **Cleanups the swap enabled:** responsive.css's `prefers-color-scheme` pill block
      and the eight `--dark-*` tokens deleted (single scheme now); calendar.css's four
      stray hex colors tokenized; `theme-color` meta added.
- [x] **Verified:** 31 WCAG pairs (text on every surface, all status chips, accent
      fills, code block) pass AA ≥ 4.5 — `--neutral-strong` alpha nudged 0.18 → 0.15
      for the disabled-fill pair. Headless-chromium walk of all 10 routes plus modal,
      task drawer, and focus states, screenshots eyeballed; eslint + 339 vitest + build
      green; design kit regenerated.

---

## GPU sharing — free VRAM after each Ollama call

- [x] **`keep_alive` on every `/api/chat` request** (`OLLAMA_KEEP_ALIVE`, default
      `2m`, new Settings field). The RTX 3060 is shared with the chess app's
      llama.cpp server; Ollama's server-side default kept `gemma4:e2b` resident
      for 5 minutes after each call. Two minutes keeps the model warm across one
      multi-call workflow (extract → match → breakdown) but frees ~8GB soon
      after. Chess-side counterpart: its llama-server now sleeps after 10 idle
      minutes; the structural fix (llama-swap, one owner for the GPU) is planned
      in `../future-plans/llama-swap.md`. New `test_ollama_provider.py` pins the
      payload wire format.

---

## Post-deploy hardening & polish (epic, 2026-07-03 → 2026-07-09)

Follow-ups from an external code review after the deployable-app epic shipped.
Two review claims were triaged out (rate-limiter "bug" was a false alarm —
defaultdict re-indexing is correct, proven by `test_rate_limit.py`; real
auth/internet exposure stays out of scope for a trusted home LAN). The four
shipped slices:

- [x] **Frontend data-consistency polish** — `useDashboard` gained `reload()` +
      `taskRefreshVersion` subscription (wired via `onTasksChanged` on the
      dashboard's capture panel); `loading` is now initial-load-only with a
      separate `refreshing` flag (no full-page spinner flash), surfaced via
      `aria-busy`; Vitest coverage for both hooks.
- [x] **Task read-path indexes** — single-column `project_id`,
      `parent_task_id`, `recurrence_id` plus compound
      `(deleted_at, review_status)` (also covers the trash scan via its leading
      column). `workflow_status` deliberately unindexed (never a SQL filter).
      Alembic migration reviewed (stripped autogen's spurious litestream table
      drops); regression-guard test asserts the index set and hot-query rows.
- [x] **Pagination on unbounded list endpoints** — `GET /api/tasks` (500/max
      1000) and `GET /api/inbox` (200/max 500) take `limit`/`offset`; services
      grew optional paging (internal callers unchanged); frontend list views
      request the max cap explicitly; pytest for paging + 422 validation.
- [x] **Rollup engine subtree scoping** — `_children_map_for(db, roots)`
      descends level-by-level over the indexed `parent_task_id` instead of
      loading the whole accepted-task table on every read; guard test covers
      multi-level trees, leak prevention, and ancestor+descendant root sets.

---

## The strip — Slices 1 + 2 (AI subsystem, training, inbox, Discord)
> Goal: rip out the AI-assisted-capture / training-data / custom-model track,
> the inbox, and the Discord bot, leaving a plain project manager. Slices 1 and
> 2 were **merged into one PR**: inbox/Discord are pure AI consumers (their
> extraction/matching/review flow imports `app.ai` and writes the training
> table), so AI could not be removed while they remained — see the merge note in
> `CURRENT.md`.

### Backend
- [x] Deleted `app/ai/` (gateway, providers, prompts, profiles, schemas,
      workflows, evals), `app/integrations/discord/`, and the routes/services
      for AI, training, settings, inbox, Discord, breakdown, review, and eval
      history.
- [x] Relocated the non-AI `GET /api/dashboard` endpoint into
      `routes_dashboard.py`; dropped the AI project-summary endpoint and the
      dashboard's inbox-insight surface.
- [x] Search and the trash service/routes trimmed to projects + tasks (inbox and
      training kinds gone).
- [x] Models: removed `InboxItem`, `EvalRun`, `AITrainingExample`, the
      `InboxSource` enum, and the `tasks.inbox_item_id` / `breakdown_output_json`
      columns. Kept `review_status`, `confidence`, `assignee_hint` (deferred — see
      `TODO.md`).
- [x] Alembic migration `019a9b406cae` drops `ai_training_examples`, `eval_runs`,
      `inbox_items`, and the two task columns (upgrade/downgrade round-trip
      verified).
- [x] Config: dropped `OLLAMA_*`, Discord, `BACKEND_SHARED_SECRET`, and the
      per-route rate-limit settings. Kept the rate-limit and write-guard modules
      (retained for Phase 2 agent endpoints; rate-limit tested in isolation).
- [x] Backend tests reworked/pruned; `./test.sh` green (pytest, ruff, mypy).

### Frontend
- [x] Deleted the `inbox`, `settings`, and `training` feature folders, their API
      clients, types, and styles; removed the break-down UI and the project-AI-
      summary panel; trimmed dashboard, search, and trash to match the new API.
- [x] Removed the corresponding nav entries and routes; Vitest, lint, and build
      green.

### Infra & docs
- [x] `main.sh` no longer starts Ollama or the Discord bot; `test.sh` dropped
      `--ai-evals`; docker-compose lost the Ollama env/host-gateway and the
      Discord profile; `.env` examples cleaned.
- [x] `README.md` and `CLAUDE.md` sections for the removed subsystems excised.

## Dashboard redo — board-first UI (epic)
> Goal: before Phase 2 (local agent), reshape the UI around doing work instead
> of summarizing it — the dashboard becomes a project-swimlane kanban, "Today"
> becomes "Focus", project task views default to the board, and the AI-era
> project aliases are removed.

- [x] **Slice 1 — project aliases removed** (#15) — Alembic migration dropping
      alias storage (upgrade/downgrade round-trip verified); alias fields and
      handling removed from schemas, services, routes, models, and the frontend
      edit surface; `README.md` schema pass.
- [x] **Slice 2 — Today → Focus rename** (#17) — backend today
      service/endpoint/schemas renamed to focus (tests followed);
      `features/today/` → `features/focus/`, nav label, `/focus` route with a
      `/today` redirect, `api/today.ts` → `api/focus.ts`; copy reframed around
      focus sessions. Project detail task views now default to kanban (hard
      default, list one toggle away).
- [x] **Slice 3 — dashboard → swimlane board** (#20; first attempt #18 reverted
      in #19 and redone from scratch) — new swimlane board component in
      `features/dashboard/` (rows = projects, Open / In progress columns,
      per-lane Done toggle fed from the completed archive; reuses `TaskCard`,
      the status-change hooks, and the `is_blocked` move rule). Lane headers
      carry project link, open count, and status tone, with a collapsed state
      for quiet projects. Signal strip above the board: overdue / blocking /
      due-today counts, clickable filters. Metric cards, workload bars,
      projects-overview table, and hero copy deleted with their CSS. Drag
      interactions verified with `verifier-browser`; `README.md` dashboard
      description updated.

## Remove the Projects list page (board is the projects surface)
> The dashboard board superseded the `/projects` grid; its unique capabilities moved.

- [x] "New project" re-homed to the dashboard board heading (opens `ProjectFormModal`);
      board empty-state opens the same modal instead of deep-linking.
- [x] "Delete project" re-homed to the project detail header (confirm + trash toast,
      hidden for protected projects); detail breadcrumb/404 fallback now target `/dashboard`.
- [x] Deleted `ProjectsPage`, its test, `useProjects`; removed the Projects nav tab and
      the sidebar per-project sub-list (and its drag-reorder/drag-to-file — board lanes
      cover both); `/projects` redirects to `/dashboard`; orphaned CSS removed.
- [x] Vitest, lint, build green; route redirect + create/delete covered by tests.

## Board-first follow-ons (post-epic polish)
> Shipped on top of the swimlane board after the dashboard-redo epic closed.

- [x] Manual project reorder (board + sidebar) and top-layer chip popovers (#21)
- [x] Spider brand icon toggles sidebar collapse (#22); mobile fix so collapse
      doesn't hide the whole nav (#23) — both superseded by the sidebar
      removal in #28
- [x] Focus timeline duplicate "part of" label + mobile card overflow fixed (#24)
- [x] Drag tasks across project lanes on the dashboard board (#25)
- [x] Close/reopen projects to hide them without deleting (#27)
- [x] Sidebar removed; navigation (Focus / Tasks / Trash) moved into the
      topbar (#28)

## Phase 2 kickoff — tasks-table cleanup, agent design, PCC MCP server (epic, 2026-07-10)
> First Phase 2 checkout: clean the dead AI-era columns out of the service
> layer, write the agent design doc, ship the PCC MCP server. Claude Code is
> PCC's first agent client.

- [x] Slice 1 — dropped `review_status`, `confidence`, `assignee_hint` from
      the tasks table (migration with upgrade/downgrade round-trip; compound
      index replaced with a plain `deleted_at` index); stripped the pervasive
      `review_status == accepted` filtering from services, schemas, routes,
      and the frontend types/surfaces (#30)
- [x] Slice 2 — agent design doc (`docs/agent-design.md`): MCP tool surface,
      guardrails, transport, dependency sign-off, explicit deferrals (#31)
- [x] Slice 3 — PCC MCP server (`backend/app/mcp/`) exposing the service
      layer as tools (task CRUD + complete, project CRUD, search, focus,
      trash/restore); guardrails (no hard deletes, boundary validation,
      `activity_events` stamped via new nullable `actor` column,
      `agent:mcp`); verified end-to-end over real stdio from Claude Code;
      `.mcp.json` auto-connects sessions in this repo (#32)
- [x] Deferred by design: dependencies/recurrence tools (follow-up in the
      next checkout), llama.cpp runtime (GPU-contention story)

---

## Phase 2 — local runtime + provider layer (epic, 2026-07-10 → 2026-07-11)
> One shared GPU server for chess + PCC, and PCC's provider speaking to it.
> Completed the MCP tool surface on the way in.

- [x] Slice 1 — MCP follow-up tools: add/remove dependency, skip/stop
      recurrence, same guardrails as the first pass; dependency add/remove
      now writes `activity_events` from every caller, UI included — a
      pre-existing audit gap (#34)
- [x] Slice 2 — sharing shape decided and stood up: llama-swap with a single
      `gemma-4-12b` entry owns the RTX 3060 (`../llama-swap/`, port 8200,
      pinned v236-cuda-b9935); ctx raised to the model's full 128k
      (`-c 131072` + q8 KV + MTP — 9.5 GB loaded / 10.5 GB peak, 125k-token
      needle retrieved); chess cut over (chess #87) and its private `llama`
      container retired; decision + measurements recorded in
      `docs/agent-design.md` (#36, #37)
- [x] Slice 3 — provider layer `backend/app/ai/providers/llamacpp.py`:
      OpenAI wire format over httpx (no SDK; httpx promoted dev → runtime
      dep, no new package), tool calling + `json_schema` structured outputs,
      Pydantic-validated at the boundary with typed errors — no best-effort
      parsing; gemma `reasoning_content` isolated from answer text and
      history; sampling set per request; `LLAMACPP_*` config + compose
      plumbing via `host.docker.internal`; unit tests over faked wire + an
      opt-in live integration (`PCC_LLM_INTEGRATION=1`) that verified a real
      tool-call round trip and structured extraction against the shared
      runtime (#38)
- [x] Epic DoD held: one GPU server serves gemma-4-12b for both apps;
      dependencies/recurrence callable from Claude Code with audit entries;
      PCC's provider completes a validated tool-call round trip
- Deferred: llama-swap phase 3 — retire host Ollama after a quiet week of
  `journalctl -u ollama` (counted from 2026-07-10)

---

## Phase 2 — agent loop, conversation persistence, chat panel, eval harness (epic, 2026-07-11)
> Assembled the shipped pieces (MCP tool surface, shared runtime, provider
> layer) into the actual agent: loop → persisted conversations → chat panel →
> eval baseline. Every mutation audited as `agent:loop`, undoable via trash.

- [x] Slice 1 — transport-agnostic tool registry (`app/tools/`): MCP server's
      tool bodies factored out and shared with the loop, schemas byte-identical
      to the MCP `inputSchema`s (parity-tested); agent loop core
      (`app/ai/loop.py`): bounded iterations, bounded self-correction on
      schema-invalid calls, terminate on text turn, actor `agent:loop`,
      request ID spans a run; scripted-provider tests, no GPU (#41)
- [x] Slice 2 — conversation persistence (`conversations` +
      `conversation_messages`, migration `7efad5645027`; tool calls/results
      stored as JSON on the assistant turn — the audit log can't reconstruct
      the trajectory) + agent REST API (`/api/agent/...`): CRUD + the one
      model-calling endpoint, user turn committed before the loop runs,
      rate-limited (`agent_messages_per_min`); text-only history round-trip;
      live-smoked on gemma-4-12b including a real self-correction (#42)
- [x] Slice 3 — chat panel (`features/agent/`, Agent nav → `/agent/:id`):
      conversation sidebar, thread with the full tool trajectory rendered
      (failed attempts included) and per-mutation undo through the same REST
      endpoints (audited, soft-delete-safe); non-streaming v1 with working
      indicator (decision recorded); browser-verified with verifier-browser
      against the live model — caught and fixed a CSS-specificity layout bug
      and a broken autoscroll sentinel (#43)
- [x] Slice 4 — eval harness (`tests/test_agent_evals.py`, opt-in
      `PCC_AGENT_EVALS=1`): six scenarios asserting trajectory shape + DB
      end-state + audit invariants against the real model; baseline 24/24
      over 4 runs recorded in `docs/agent-design.md` — FTS5 retrieval found
      the described task every run (no embeddings case); self-correction
      recovered live in 3 of 4 runs (#44)
- [x] Epic DoD held: panel → loop → correct tool calls, every mutation
      audited + undoable, conversations survive reload, evals green with
      baseline recorded, `./test.sh`/CI green throughout
- Decisions: registry `app/tools/`, loop `app/ai/loop.py`, actor
  `agent:loop`; trajectory persisted on the message; text-only model
  context; non-streaming v1 (SSE only if usage demands)

---

## Agent UX — polish + ambient entry (epic, 2026-07-11)
> Made the shipped agent pleasant and reachable: markdown replies, entity
> links in the tool trajectory, the spider mascot, and agent entry straight
> from the command search bar. Decisions recorded at checkout: no reply-text
> linkification (trajectory rows link from persisted ids); static mark +
> working-state bob only; voice (#287) + personality (#290) deferred as a
> pair toward a shared workspace companion layer.

- [x] Slice 1 — mobile task-edit chip popovers no longer flash closed on
      tap: scroll/resize re-anchor instead of dismissing, outside-press
      moved to capture phase; jsdom tests for the tap/keyboard sequences +
      verifier-browser pass on iPhone emulation (#47, prod task #293)
- [x] Slice 2 — assistant replies render as markdown (`react-markdown@10`,
      agent bubble only, dependency approved + locked); tool-trajectory rows
      link to what they touched via pure `linkFor()` (undone rows re-route,
      failed calls never link); reply-text linkification resolved: no —
      trajectory links suffice (#48, prod task #291)
- [x] Slice 3 — shared `SpiderMark` component replaces the stock Bot avatar
      everywhere (brand, Agent nav, panel avatar, working indicator, empty
      state); CSS-only working-state bob, reduced-motion guarded (#49, prod
      task #289)
- [x] Slice 4 — ambient agent entry: plain Enter / "Ask the agent" in the
      command search posts to a fresh conversation and renders the exchange
      inline (one rendering surface — the panel's components); "Continue in
      Agent" opens `/agent/:id`; slash commands deleted wholesale (parser,
      tests, hints, dead CSS) (#50, prod task #292)
- [x] Follow-ons: parent-dropdown row overlap + mobile scroll-to-bottom fix
      (#51); topbar brand got its own web mark so the spider reads as the
      agent, not the app (#52)
- [x] Epic DoD held: search bar → inline exchange → markdown reply +
      trajectory links → same conversation on `/agent`; mobile pills bug
      dead on a real mobile viewport; prod tasks #289/#291/#292/#293 done
      (verified against the prod instance 2026-07-11); `./test.sh`/CI green
      throughout

---

## Fleet agent-standard alignment (2026-07-11)
> Phase 1 of the workspace agents master plan
> (`../agent-standard/AGENTS-MASTER-PLAN.md`) — PCC is the standard's
> reference implementation, so alignment was the smallest delta in the fleet:
> discovery, layered personality, and delegate attribution.

- [x] `agent:` block in `app.yaml` (description, `api: /api/agent`, six
      examples) per `../agent-standard/app-yaml-agent-block.md`, so conductor
      discovers PCC's agent and builds its delegate tool without any
      conductor code change (#54)
- [x] Layered personality: the loop's single hardcoded system prompt became
      the standard's layered composition (`STANDARD.md` §5) — app base
      prompt → global Glitch, vendored verbatim at
      `backend/app/ai/personality-global.md` (canonical in `agent-standard/`,
      drift checked by `check-sync.sh`) → per-run date injection; no
      app-flavor layer, nothing has earned one. Layer order/presence
      unit-tested; eval harness re-run green under the layered prompt
      (2 consecutive suites, 12/12) — Glitch's brevity did not degrade tool
      honesty (#55)
- [x] `X-Agent-Actor` delegate attribution on `POST …/messages`:
      `resolve_actor` (`app/ai/loop.py`) stamps a recognized delegate actor
      (`agent:conductor`) into `activity_events`; absent or unrecognized
      values fall back to `agent:loop` per the contract's
      ignore-unknown-actors rule, so a caller can't stamp an arbitrary
      identity. Tests cover conductor attribution, the fallback, and the
      404-on-missing/soft-deleted-thread semantics conductor's
      recreate-and-retry-once depends on (#56)

## Voice (VOICE-PLAN Phase 3, 2026-07-12)
> A spoken "add a task to buy milk tomorrow" lands as a real task from the chat panel or the ambient search bar, reply spoken back. Closes long-deferred #287 via `../agent-standard/voice.md`.

- [x] Voice backend: `app/ai/speech.py` `SpeechClient` per the fleet voice
      standard (OpenAI audio wire over plain httpx, Pydantic-validated,
      typed errors, PCC task/project/date STT vocabulary prompt) +
      `/api/voice/transcribe` & `/api/voice/speak` proxies to the shared
      `../speech/` service, rate-limited per IP like the agent surface
      (`VOICE_REQUESTS_PER_MIN`); 20 boundary tests on `httpx.MockTransport`
      fakes (#58)
- [x] Voice in the agent chat panel: chess-canonical frontend modules
      vendored verbatim (`src/voice/{vad,wav,tts,MicButton}`, drift checked
      by `agent-standard/check-sync.sh`) with a PCC-owned `src/voice/api.ts`
      adapter; push-to-talk + hands-free half-duplex conversation mode;
      voice in → voice out, typed in → silent; localStorage voice-output
      toggle; local-first VAD assets (`scripts/copy-vad-assets.mjs`) and a
      Vite dev proxy for `/api`. Browser-verified end-to-end: spoken phrase
      → transcribe → agent → real task + spoken reply (#59)
- [x] Voice entry on the ambient search bar: vendored MicButton in
      `.command-search`, transcript down the same inline-ask path as typed
      text, voice-initiated asks spoken, typed Enter silent; eval harness
      re-run green (6/6 twice — voice adds no prompt-visible change) (#60)
