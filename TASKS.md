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
- [~] Consistent empty / loading / error states across pages
- [ ] Toasts for success / failure
- [~] Shared component layer in `src/components/` — `AppShell` added; reusable inbox capture panel lives in `features/inbox`; primitive Button/Card/Badge still implicit in CSS

Revamp follow-up fixes (from review of the Sprint 7 revamp):
- [x] Dashboard: drop the duplicate pending-inbox fetch in `useDashboard` — the capture panel already loads pending items and reports the count via `onPendingCountChange`
- [x] Dashboard: replace the local `weekDistance` helper with the shared `dueStatus(due, 7)` from `utils/dates` (no duplicated date math)
- [x] Dashboard: remove the dead `status !== 'done'` guard on blocked tasks — `GET /api/tasks` returns accepted-only, so it never filtered anything
- [x] Dashboard: time-aware hero greeting (morning / afternoon / evening) instead of a hardcoded "Good morning"

### Task & Inbox UX overhaul (added — 8 small slices, ship as separate PRs)
> Goal: clickable task/project detail views built on a shared task card, smarter ordering,
> a task filter, customizable estimates, and an inbox that is review-only with per-candidate
> approval. Detailed working steps live in `TASKS_SPRINT.md`. Slices 5→6→8 are ordered (the
> shared card and detail view are prerequisites for the inbox rework). Backend touches only
> Slices 3 and 8; **no schema/migration** in any slice.

- [X] **Slice 1 — Ordering + nesting integrity** (FE-only): sort tasks by due date, then by
      priority (`urgent` > `high` > `medium` > `low`) as the tie-breaker; keep subtasks grouped
      under their parent at every level. Extend `utils/dates.compareByDue` → `compareTasks`
      (priority rank as secondary key, `id` last) + unit tests. _Requests: order-by-priority,
      keep-nested-together._
- [X] **Slice 2 — Nav + project-name links** (FE-only): remove **Tasks** from the `AppShell`
      sidebar `primaryNav` (the dashboard Open Tasks card already links to `/tasks`; the route
      stays); in the global task view render the real project **name** (from the already-loaded
      `projects`) instead of `Project #{id}`. Update `AppShell.test.tsx`. _Requests: drop Tasks
      tab, real project names._
- [X] **Slice 3 — Subtask project inheritance** (BE + pytest): in `services/tasks.create_task`,
      when `parent_task_id` is set and `project_id is None`, inherit the parent task's
      `project_id` (before the accepted→General fallback). Owns the logic in Python (prime
      directive #1). Happy-path pytest. No migration. _Request: fresh subtasks inherit parent
      project._
- [X] **Slice 4 — Customizable estimate input** (FE-only): replace the fixed `DURATION_OPTIONS`
      dropdown in the task edit form with a custom estimate input; later upgraded in Sprint 9
      to natural text parsing (`30m`, `2h`, `1 day`, `none`). Backend already enforces
      `estimated_minutes > 0`. Keep `formatDuration` for the list/card badge.
      _Request: fully-customizable time estimate._
- [X] **Slice 5 — Shared `TaskCard` component** (FE-only, foundational): `features/tasks/TaskCard.tsx`
      — a clickable card rendering title / workflow status / priority / due badge / estimate /
      Blocked / project, navigating to the task detail view on click. Used by the task list,
      project view, and inbox. CSS in `index.css`. _Request: better-looking clickable card
      (shared)._
- [X] **Slice 6 — Task detail view + add/edit modal unification** (FE + small BE): route
      `/tasks/:taskId` → `TaskDetailPage` showing the task's editable fields, its subtasks (as
      `TaskCard`s, each click-through to its own detail view), and dependencies; clicking a task
      in a project opens the same view. Add **`GET /api/tasks/{id}/subtasks`** (BE, thin wrapper
      over `list_subtasks`) so a detail view can load its direct children regardless of status.
      Make the **Add task** button open a modal that reuses the task form (create mode). _Requests:
      special task view + subtasks, subtask/project-task click-through, projects similar style,
      add-task modal._
- [X] **Slice 7 — Task filter** (FE-only): a filter bar on the task view (workflow status,
      priority, project [global view], plus due-soon/overdue/blocked toggles), filtering
      client-side over loaded tasks while preserving parent→child grouping. _Request: filter
      for tasks view._
- [X] **Slice 8 — Inbox = review-only, per-candidate approval** (BE + FE): drop the capture panel
      from `/inbox` (capture stays on the dashboard); show notes awaiting review with their
      candidates rendered as the **same `TaskCard`s**; open a candidate → edit it → **Submit**
      (approve that one) or **Dismiss** (reject it). Backend: refactor `services/review.py` into a
      per-candidate decision (approve = review_status→`accepted` + project resolve via existing
      `PATCH /api/tasks/{id}` for edits; dismiss = review_status→`rejected`) that **finalizes the note**
      — sets `reviewed_at` and writes the single `ai_training_examples` row (+ the
      `project_matching` row) — only when no `candidate`-status tasks remain, preserving the
      one-row-per-note invariant (prime directive #4). pytest for per-candidate + finalization; Vitest for
      the inbox cards. _Requests: inbox shows only awaiting-approval as task cards, approve one at
      a time._

---

## Sprint 9 — Task Detail Workspace & Status Split
> Goal: stop leaking backend review lifecycle into normal task management, and make the
> individual task page the primary place to edit task fields directly.

### Backend state model
- [x] Split task state into `review_status` (`candidate` / `accepted` / `rejected`) and
      `workflow_status` (`open` / `in_progress` / `done`)
- [x] Alembic migration `9b2c1d7e4a6f` — rename old `tasks.status` to `review_status`, add
      `workflow_status`, migrate old `done` rows to `review_status=accepted` +
      `workflow_status=done`, and upgrade the live SQLite DB to head
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
- [x] Add `TaskDetailPage.test.tsx` coverage artifact for no Edit button, hidden review status,
      inline title save, workflow status save, and friendly estimate save

### Friendly estimates
- [x] Add `parseDurationInput` / `formatDurationInput` in `utils/duration.ts`
- [x] Estimate inputs now accept natural text: `30m`, `45 min`, `2h`, `2 hours`, `1 day`,
      `1 week`, plain numbers as minutes, and empty / `none` to clear
- [x] Use the friendly estimate input in task detail, `TaskFormModal`, and the older
      `TaskEditModal`
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
- [x] TaskDetailPage subtask form — same rich inline form upgrade (no "More options"
      needed since all fields are already editable on the detail page)
- [x] CSS: `.task-subtask-fields` + `.task-subtask-actions` layout classes for
      compact field/button rows; fixed title input height (was setting 260px on
      a column-direction flex)
- [x] Backlog entry in README: Sprint 11 (backlog) for AI "break this down" feature
      (decompose task → suggest subtasks via AI)

---

## Sprint 9d — Inbox Approval UX Overhaul
> Goal: make per-note candidate review reliable and friendly. Shipped as 7 small,
> independently testable chunks. **No schema/migration; no AI-workflow/eval change** —
> the existing `decide`/`review` endpoints already write training data on finalize.

- [x] **Chunk 1 — Reappear bug fix** (BE + pytest): `GET /api/inbox/{id}/candidates`
      returned every active task for the note regardless of `review_status`, so
      approved/dismissed candidates came back when the user left and returned (and
      re-deciding them 400'd). `services/inbox.list_candidates` gained an optional
      `review_status` filter; the route now passes `candidate` while the finalization
      path keeps the unfiltered view it needs for the `accepted` rows. Regression test
      `test_candidates_endpoint_excludes_decided_tasks`; 5 existing tests re-read decided
      state via `GET /api/tasks/{id}`. _Request: fix decided tasks reappearing._
- [x] **Chunk 2 — Candidate-mode editor + breadcrumbs** (FE): a candidate's `TaskCard`
      opens `/tasks/:id` in candidate-mode — **Approve** / **Dismiss** (call
      `decideCandidate`, then navigate back to the note, or `/inbox` if it was the last)
      replace **Mark done** / **Delete**; subtasks/dependencies hidden. Breadcrumb
      `Inbox › Note review › <title>`. Note review is now an addressable
      `/inbox/:inboxId` route (`useInbox.selectItemById`). Approve sends `project_id`
      only when set, so the backend's suggested-project fallback still applies. _Requests:
      approve button in the edit window, drop the complete button, breadcrumb back._
- [x] **Chunk 3 — Bulk Approve all / Dismiss all** (FE): note-review buttons that decide
      every remaining candidate at once via `POST /api/inbox/{id}/review`. _Improvement._
- [x] **Chunk 4 — Surface model signals** (FE): per-candidate `conf 0.xx` badge
      (candidate-only) + suggested-project chip; candidates sorted lowest-confidence-first
      so the riskiest extractions surface first. _Improvement._
- [x] **Chunk 5 — Polish bundle** (FE): "N remaining to review" counter, `confirm`
      before the destructive "Dismiss note", and a post-finalize "View filed tasks" link.
      _Improvement._
- [x] **Chunk 6 — URL-based note navigation** (FE): clicking a note routes to
      `/inbox/:id` (was local state), so browser-back returns to the inbox list instead
      of the dashboard; added a `← Inbox` breadcrumb on the note view. _Request: get back
      to the inbox after opening a note._
- [x] **Chunk 7 — Repair stale frontend tests** (tests only): pre-existing failures
      surfaced by Sprint 9b/9 — added missing `listCompletedTasks`/`reopenTask` to the
      `api/tasks` mocks (TasksPage, DashboardPage) and drove the "Done" view through
      `listCompletedTasks`; scoped the ambiguous `getByText('Open')` to the status pill;
      updated the dashboard test to the "Awaiting Review" metric card (inline pending-list
      was removed). Frontend 86/86, backend 148/148.

---

## Sprint 10 — Custom Model Training
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
- [x] Bulk accept/reject in review queue (Sprint 9d Chunk 3 — Approve all / Dismiss all)
- [ ] Dark mode
- [ ] Export tasks to markdown
- [ ] `docker-compose.yml` — backend + frontend in containers (deferred from Sprint 6)
