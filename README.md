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
project_aliases
tasks                  (includes review_status: candidate | accepted | rejected;
                        workflow_status: open | in_progress | done;
                        nullable parent_task_id self-FK for subtask nesting;
                        nullable estimated_minutes effort estimate;
                        nullable breakdown_output_json holding the "break this down"
                        model output between generating and reviewing subtasks)
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
Deleting any other project rehomes its active tasks to `General` before the
project is soft-deleted, and the top-level `/tasks` view lists accepted work
across projects so dashboard counts always point to reachable tasks.

Tasks nest via a nullable self-referential `parent_task_id` (a tree, not a graph:
a self-/ancestor-cycle is refused with a `409`, guarded in `services/tasks.py`).
Soft-deleting a parent **cascade-soft-deletes its whole subtree**; restore is
per-task (restoring a parent does not auto-restore children — each is restorable
from `/trash`). Ordering tasks is separate from nesting: `task_dependencies` holds
`A depends_on B` edges meaning **B must be workflow-`done` before A can start**.
"Blocked" is never a stored status — it's derived in Python from the active edges
and the depended-on tasks' workflow status (`TaskRead.is_blocked`, resolved in one bulk query), and
the same `services/task_dependencies.py` cycle guard refuses any edge that would
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
           Capture-hygiene slice: dismiss/clear inbox items
           (DELETE /api/inbox/{id} soft-delete + per-item Dismiss button; training
           examples preserved, no migration); alias management UI (add/remove
           aliases in the project edit modal over the Sprint 4 alias endpoints,
           frontend-only); trash/restore (aggregate GET /api/trash + per-entity
           POST .../restore for projects/tasks/inbox, /trash page; inbox restore
           409s on a re-captured-hash collision; restored tasks rehome to General;
           no migration). Task-model slice (separate PRs): task nesting —
           self-referential tasks.parent_task_id (migration f83c22ab757c),
           Python cycle guard (no A→B→A → 409), cascade soft-delete of the
           subtree, indented subtask display + Parent-task dropdown. Task duration
           estimate — nullable tasks.estimated_minutes (migration d036d1c48a82,
           Pydantic gt=0), human-label dropdown + list badge via utils/duration.ts.
           Task dependencies — task_dependencies table (migration 3263531ae531,
           "A depends_on B" = B done before A starts), Python DFS cycle guard
           (self/dup/A→B→A → 409), derived is_blocked (no status column; bulk
           query, no N+1), GET/POST/DELETE /api/tasks/{id}/dependencies, "Depends
           on" modal section + red Blocked badge. UI refresh: persistent command
           center shell with lucide icons, dashboard focus cards, contextual plus
           controls for adding tasks/projects, and a reusable messy-text
           capture/review panel embedded at the top of the dashboard and reused
           by `/inbox`; the old Quick Actions card was removed (no new backend routes).
Sprint 8:  [DONE] Task & Inbox UX overhaul — 8 slices:
           (1) compareTasks: due-then-priority sort at every tree level.
           (2) Tasks tab removed from nav; project links show real name.
           (3) Fresh subtasks inherit parent's project (BE + pytest).
           (4) Custom estimate input replacing the fixed dropdown;
               formatDuration/splitDuration/toMinutes in utils/duration.ts.
           (5) Shared TaskCard component (link to /tasks/:id, badges, actions prop).
           (6) TaskDetailPage (/tasks/:taskId) with subtask cards + edit modal;
               GET /api/tasks/{id}/subtasks route; TaskFormModal (create + edit modes).
           (7) Client-side task filter (workflow status, priority, project,
               overdue/due-soon/blocked).
           (8) Inbox = review-only (no capture textarea); candidates as TaskCards;
               POST /api/inbox/{id}/candidates/{task_id} per-candidate approve/dismiss;
               finalization + training row written once all candidates are decided.
Sprint 9:  [DONE] Task detail/status redesign — split task state into
           review_status (candidate/accepted/rejected) and workflow_status
           (open/in_progress/done), keep blocked derived from dependencies, migrate
           existing rows, and update dashboard/list/dependency/summary semantics.
           Rebuilt /tasks/:taskId as an inline-editable workspace: no Edit button,
           editable title/description/priority/due/project/parent/status/estimate,
           polished header, dependency rows, subtask section, and save/error states.
           Estimate entry is now natural text: 30m, 2h, 1 day, none.
Sprint 9b: [DONE] Completed-task archive — done tasks leave the active list and
           are reachable via the "Done" option in the /tasks status dropdown,
           which swaps the list to the completed archive (lazily fetched from
           GET /api/tasks?workflow_status=done and GET /api/projects/{id}/tasks?
           workflow_status=done). POST /api/tasks/{id}/reopen sends a task back to
           open. The same dropdown gained a "Blocked" filter (client-side over
           is_blocked). Mirrors the trash/restore pattern.
Sprint 9c: [DONE] Rich inline subtask form — add subtask composers in TasksPage
           and TaskDetailPage now expose optional priority/due_date/estimate fields
           inline (reuses parseDurationInput for friendly text: 30m, 2h, 1 day).
           TasksPage adds a "More options" link to hand off the draft to TaskFormModal
           for description/project/status. TaskFormModal create mode now seeds
           title/priority/due_date/estimated_minutes from optional defaults prop.
           CSS updates: .task-subtask-fields + .task-subtask-actions layout classes.
Sprint 9d: [DONE] Inbox approval UX overhaul. Bug fix: GET /api/inbox/{id}/candidates
           now returns only undecided (review_status=candidate) tasks, so approved/
           dismissed candidates no longer reappear after leaving and returning to a
           note (services/inbox.list_candidates gained a review_status filter; the
           finalization path still sees all rows; pytest regression). Candidate editor:
           a candidate's TaskCard opens /tasks/:id in candidate-mode — Approve/Dismiss
           (calls the decide endpoint, then returns to the note) replace Mark-done/
           Delete, with an Inbox › Note review › title breadcrumb; note review is now an
           addressable /inbox/:inboxId route. Bulk Approve all / Dismiss all on a note
           (reuses POST /api/inbox/{id}/review). Model signals: per-candidate confidence
           badge + suggested-project chip, candidates sorted lowest-confidence-first.
           Polish: "N remaining to review" counter, confirm-before-Dismiss-note, and a
           post-finalize "View filed tasks" link. Navigation: clicking a note routes to
           its URL so browser-back returns to the inbox (was jumping to the dashboard),
           plus a ← Inbox breadcrumb. No schema/migration, no AI/eval change. Also
           repaired stale frontend tests (listCompletedTasks mocks, status-pill query,
           dashboard pending-card assertion).
Sprint 9e: [DONE] Projects tab UX overhaul — brought Projects to par with the
           Tasks/Inbox polish. Frontend-only: no schema/migration, no new/changed API
           route (reuses existing project/task/activity/summary/alias endpoints).
           Card-based list (ProjectCard reusing .task-card) replacing the bare list;
           new inline-editable hub at /projects/:id (name/description save-on-blur,
           AI summary via GET /api/projects/{id}/summary, activity feed, alias
           add/remove, tasks as TaskCards, "View all tasks" → kept /projects/:id/tasks
           board); ProjectFormModal (create/edit) replacing the inline form + retiring
           ProjectEditModal; per-project open/done counts + progress bar + derived
           status badge (shared utils/projectStatus.ts, also used by the dashboard
           Projects Overview); client-side search + sort; confirm-before-delete and
           consistent empty/loading/error states.
Sprint 9f: [DONE] Trash tab UX overhaul — brought /trash to par with the
           Tasks/Inbox/Projects polish (cards, lucide icons, context badges,
           "Deleted X ago", search + type filter, nav count, bulk Restore all,
           restore notices). Schema-surface change: added deleted_at to ProjectRead/
           TaskRead/InboxRead (serializes null for active rows; no migration — it
           reads the existing SoftDeleteMixin column). New routes: DELETE
           /api/{projects,tasks,inbox}/{id}/purge (permanent delete — 404 absent /
           409 active / 403 protected General) + DELETE /api/trash (empty trash,
           returns per-kind counts). Purge is the one true delete (user-approved
           override of "soft deletes only"); it only ever removes rows already in
           trash, cleans every FK edge explicitly (FK enforcement is off on SQLite),
           and leaves ai_training_examples untouched (no FK). No Alembic migration
           (purge is DML, not a schema change). [Superseded by 9i: training
           examples can now themselves be trashed and purged — empty-trash purges
           any that are *in trash*, but never the active corpus.]
Sprint 9g: [DONE] Settings tab UX overhaul — brought /settings to par with the
           Tasks/Inbox/Projects/Trash polish (6 chunks). FE: page header, sticky
           section nav (Profiles · Prompts · Evals), card layout + lucide icons;
           per-editor dirty-state (Save gated on real changes, unsaved dot,
           beforeunload guard) + transient "Saved ✓" confirmation; prompt editor
           upgrades (workflow tag derived from profiles, monospace/resizable
           textarea, live char count, revert-to-last-saved); eval pass-rate trend
           across recent runs + "Run all suites" button; live Ollama health panel
           (connected/host + re-check) and an installed-model dropdown in
           ProfileEditor (free-text fallback, preselects current value, never
           silently re-defaults task_extraction off gemma4:e2b); reset-to-default
           for profile overrides. BE: three settings routes — read-only GET
           /api/settings/ollama/status and GET /api/settings/models (Ollama
           introspection via gateway/provider only, public reads), plus
           loopback-guarded DELETE /api/settings/profiles/{name}/overrides
           (optional ?field= clears one field, no field clears all; removes keys
           from profiles.local.yaml and returns the new effective ProfileRead).
           No schema/migration. In-app route-change blocking deferred (needs a
           createBrowserRouter conversion); flaky TaskDetailPage.test.tsx noted as
           pre-existing.
Sprint 9i: [DONE] Training-data pruning — let the user clean junk rows out of the
           corpus via the same two-step trash → purge path as every other entity.
           BE: training_data gains soft_delete/restore/purge helpers (leaf table,
           so purge is a bare hard_delete, restore has no conflict); three routes
           DELETE /api/training-examples/{id} (soft-delete), POST .../restore,
           DELETE .../purge (404 absent / 409 active-not-trashed). Registered as a
           fourth trash kind: TrashRead/EmptyTrashResult/TrashCountResult and
           empty_trash gain training_examples; deleted_at added to TrainingExampleRead
           (no migration — reads the existing SoftDeleteMixin column). A soft-deleted
           example drops out of the /training list AND the progress-to-200 meter
           automatically (both already filter deleted_at IS NULL). User-approved
           exception to "treat training data like accounting data" — pruning is only
           ever via reversible trash, and the active corpus is never destroyed in
           bulk. FE: per-example "move to trash" button on /training; a Training
           examples section on /trash (restore / delete-forever), nav count + empty-
           trash include it. New route + trash tests; pytest green. No Alembic.
Sprint 9j: [DONE] UX foundation + global search. Foundation: shared primitives in
           src/components (Button / Card / Badge built on the existing tone palette,
           AsyncState for the loading/error/empty baseline) + a toast system
           (ToastProvider + useToast, mounted in App) retrofitted onto the
           task/project/inbox mutation hooks. Feature: global search — GET /api/search?q=
           runs a deterministic, wildcard-escaped LIKE over active projects, tasks, and
           inbox items (new services/search.py + schemas/search.py, grouped+capped
           results, no schema/migration/model call). The topbar command bar is now live
           (CommandSearch): debounced query, grouped dropdown on the shared
           Card/Badge/AsyncState, keyboard nav, click-through (task→/tasks/:id,
           project→/projects/:id, inbox→/inbox/:id). Input kept generic for future
           /done, /new, and AI chat. New search + CommandSearch tests; pytest green.
Sprint 9k: [DONE] Today / daily schedule — a deterministic /today view that turns
           accepted, not-done tasks into a plan for the day. Pure Python scheduler
           (services/today.py): ranks by in-progress→open, due urgency, priority,
           then shorter-as-tiebreaker; packs sequential blocks until capacity is
           exhausted; surfaces overflow in ranked order and blocked tasks (unfinished
           dependency) separately, never scheduled. GET /api/today with date/
           start_time/available_minutes, validated at the boundary. Frontend /today
           page (timeline + overflow + blocked + empty states) reached from the
           dashboard "Today's Tasks / Due Soon" tile; not added to the sidebar.
           Missing estimates default to 30 min, labelled "assumed". New backend
           today + route tests and a TodayPage test; pytest green. No model call,
           no schema change, no Alembic, no new dependency.
Sprint 9L: [DONE] Recurring task stubs — optional recurrence on tasks. New
           tasks.repeat_interval (JSON {unit: day|week|month, every: 1-12}, null =
           non-recurring) + tasks.recurrence_id (char(36), shared across a series);
           Alembic migration. Pure-Python service layer (services/tasks.py): marking
           a recurring task done auto-creates the next occurrence (due date advanced
           by the interval, month math day-clamped — Jan 31 + 1mo → Feb 28, no
           dateutil dependency), accepted + open + top-level, copying the recurrence_id.
           PATCH /api/tasks/{id} gains repeat_interval, skip_recurrence (suppress next
           occurrence on completion), and edit_scope ("this" | "future" forward-patches
           same-series rows due on/after this one). repeat_interval requires a due_date
           (422). Frontend: RepeatIntervalInput (natural text — "weekly", "every 2
           months"), EditScopeModal, "Skip this occurrence" on TaskDetailPage, a repeat
           badge on TaskCard. New backend recurrence tests + frontend Recurrence tests;
           pytest green, no model call, no new dependency.
Sprint 9m: [DONE] Command-bar slash actions — the generic CommandSearch topbar now
           switches from search to an action on a leading "/". A pure parser
           (features/search/parseCommand.ts) maps input to a discriminated command:
           "/new <text>" captures the text to the inbox and runs extraction, then
           navigates to the note-review route (reuses createInbox + processInbox; an
           in-flight lock blocks a double-submit; server-side input-hash dedupe makes
           repeats idempotent); "/done <query>" reuses GET /api/search and lists only
           the matching tasks, completing the chosen one via POST /api/tasks/{id}/done
           (the dedicated endpoint, so recurrence's next-occurrence creation is
           preserved); a bare "/" (or an argument-less verb) shows a disabled hint row.
           Search results, the /new confirm row, and /done matches are all unified
           ActionRows in one keyboard-navigable list. To let /done offer only valid
           targets, SearchResultItem gained review_status/workflow_status (serialized
           off existing Task columns — no migration, null for projects/inbox); the bar
           filters to accepted + not-done tasks. New parseCommand unit tests + extended
           CommandSearch/search tests; pytest + npm run test green. No model call, no
           schema change, no Alembic, no new dependency.
Sprint 9n: [DONE] Today / daily schedule actionability — made the read-only /today
           view the place you run your day from. Slice 1 (frontend-only): in-row
           Start (→ in_progress via PATCH /api/tasks/{id}) and Mark done (via the
           dedicated POST /api/tasks/{id}/done, so recurrence's next-occurrence
           creation is preserved) on every scheduled + overflow row; in-progress rows
           hide Start; each action refetches the plan (reused useTodayPlan().refetch)
           so the row re-ranks or drops out, with per-row pending state and useToast
           errors. Slice 2 (serialization + frontend): BlockedTask.blocking_task_ids
           (list[int]) replaced by blocking_tasks (list[BlockingTask] = task_id +
           title + workflow_status), populated from the existing dependency loop;
           /today blocked rows now show each blocker's title + status pill linking to
           /tasks/:id instead of bare #id links. New backend today/route tests +
           TodayPage action/blocked tests; pytest green. No model call, no eval change,
           no schema/migration, no Alembic, no new dependency.
Sprint 9o: [DONE] Command bar completion — finished the two stubbed behaviours of
           the CommandSearch topbar. Slice 1 (frontend-only): the advertised "Cmd K"
           hint is now real — a window keydown listener matches Cmd/Ctrl+K from any
           route, preventDefault's the browser's own binding, then focuses + selects
           the input and opens the dropdown (Escape still blurs via the existing
           onKeyDown). Slice 2 (backend, pure SQL/Python — no model call): global
           search now ranks by relevance instead of newest-first. A SQLAlchemy case()
           score per kind orders exact title/name matches above prefix above substring
           above description/raw-text-only, with id desc as the recency tiebreak; for
           tasks a separate state tie-breaker floats accepted + not-done work above
           done/candidate noise only within the same text tier. SearchResults payload shape
           is unchanged (only ordering within each group differs), so the frontend needs no
           change. New Vitest shortcut tests + pytest ordering tests; pytest green,
           Vitest green (pre-existing ProjectDetailPage flake aside). No schema change,
           no Alembic, no eval/Pydantic obligation, no new dependency.
Sprint 10a: [DONE] AI "break this down" — a second correctable AI surface that
           feeds the training corpus. Per-task POST /api/tasks/{id}/break-down runs
           a new break_down_task workflow (ai/workflows/break_down_task.py): gateway
           call through the break_down_task profile → Pydantic BreakdownOutput
           validation → candidate subtasks created as children via
           create_task(parent_task_id=...) (project inherited from the parent).
           Idempotent (existing candidate children or a pending breakdown short-
           circuit the model call); invalid output records a training-failure row and
           returns 422. Reviewed on TaskDetailPage — suggested subtasks render as
           TaskCards with Approve/Dismiss; POST /api/tasks/{id}/breakdown/review
           (services/breakdown.py) approves (flip to accepted, with edits) or
           dismisses (soft-delete), and once all are decided writes exactly one
           ai_training_examples correction row (full input/output/corrected) and
           clears the holding column. New tasks.breakdown_output_json nullable column
           + migration 5b5f79d37b6e (holds the original model output between
           generate- and review-time so the correction can be captured — directive
           #4). New break_down_task profile + ai/prompts/break_down_task.md + eval
           suite (ai/evals/breakdown_cases.yaml + run_breakdown_evals.py, registered
           in the settings eval runner; 6/6 on gemma4:e2b). No new dependency.
Sprint 10b: [DONE] Internal read-only Calendar view — a month/week calendar over
           tasks.due_date at /calendar. New GET /api/calendar?start=&end=
           (services/calendar.py + routes_calendar.py): deterministic, date-bounded
           read of accepted tasks with a due date in range — includes done (so
           completed work shows on past days), excludes candidate/rejected and
           soft-deleted; end<start → 422. Returns a flat list[TaskRead] reusing the
           existing _reads_with_blocked serializer (is_blocked resolved in one
           query). Frontend features/calendar/ (useCalendar hook deriving the
           full-week grid range in local time — no UTC off-by-one; CalendarPage with
           month grid / week toggle, prev/next/today, task chips linking to
           /tasks/:id). Reached ONLY via the dashboard "Upcoming Events" rail tile,
           which is now real (soonest-due tasks + a working View calendar link,
           replacing the "Calendar not connected" placeholder) — no global-nav entry,
           mirroring /today. NOT external Google/iCal sync (stays on do-not-build).
           New backend test_calendar.py + frontend CalendarPage tests; dashboard test
           updated. No schema/migration, no Alembic, no model call, no new dependency.
Sprint 11: [DONE] Kanban board — a drag-to-move board view over workflow_status
           (open/in_progress/done) on the existing TasksPage, both global (/tasks)
           and per-project (/projects/:id/tasks), via a ?view=board toggle (seeded
           from the URL, written back on toggle). New features/tasks/KanbanBoard.tsx:
           three flat-card columns reusing TaskCard; native HTML5 drag-and-drop
           (draggable cards + column drop zones) plus a per-card "Move to" <select>
           for keyboard/a11y. The Done column is sourced from the completed archive
           (useCompletedTasks, now also enabled in board mode); Status/Sort filters
           hide in board mode (the board lays out by status, sorts by compareTasks),
           every other filter still applies. Moves route to the right endpoint:
           into Done → recurrence-safe POST /api/tasks/{id}/done; out of Done →
           reopen (→ open, then PATCH when targeting In progress); else
           PATCH /api/tasks/{id} {workflow_status}. A derived-is_blocked task is
           refused entry to In progress/Done with a toast (mirrors the list/Today
           rule). New KanbanBoard.test.tsx (column layout, move routing, blocked
           guard, done→out); tsc/eslint/vitest/build green. Frontend-only: no new
           backend route, no schema/migration, no Alembic, no model call, no new
           dependency.
Sprint 12: [DONE] Recurring series management — see a whole recurrence series and
           stop it deliberately. New GET /api/tasks/{id}/series returns every
           occurrence sharing a recurrence_id (TaskSeries: recurrence_id +
           occurrences[TaskRead]), oldest due date first, INCLUDING soft-deleted
           skipped rows (plain select(Task), not the active() helper, so the
           timeline is truthful). New POST /api/tasks/{id}/stop-recurrence clears
           repeat_interval while leaving recurrence_id intact (matches the inline-
           clear rule), so completing the task no longer spawns the next occurrence;
           422 if not recurring. New services/tasks.py get_series + stop_recurrence.
           Frontend: lazy-loaded RecurrenceSeries panel on TaskDetailPage (shown when
           recurrence_id is set) — Show/Hide occurrences timeline with state pills
           (open/in-progress/done/skipped, current row highlighted) + a confirm-gated
           Stop recurrence button. New api/tasks.ts getTaskSeries/stopRecurrence,
           TaskSeries type. Tests: test_recurrence.py (series order incl. skipped,
           stop clears repeat/keeps id/no respawn, 422 paths, HTTP happy-paths);
           Recurrence.test.tsx (lazy load + current/skipped marking, stop confirm).
           No schema/migration, no Alembic, no model call, no new dependency.
Sprint 13: [DONE] AI subsystem quality — three cohesive AI-workflow polish items.
           (1) Eval regression warning: the Settings → Evals trend now shows a red
           "▼ regressed" pill when a suite's latest pass rate dropped below its
           previous run (pure frontend over already-fetched eval_runs history; no
           backend change). (2) Prompt snapshot on save: put_prompt copies the
           current prompt to ai/prompts/.history/<name>.<UTC-timestamp>.md before
           overwriting (microsecond precision so same-second saves don't collide;
           .history/ is gitignored and excluded from list_prompts' *.md glob), so a
           score drop after an edit can be diffed/reverted manually. (3) Training
           corpus QA filters: list_examples gained a status filter mirroring the
           frontend 3-way taxonomy (corrected / accepted / failure) + a model_profile
           filter (replacing the old accepted-bool param), and /stats now returns the
           distinct sorted profiles list; the Training page status dropdown is now
           3-way and a Profile dropdown was added. Tests: training filter/stats +
           prompt-snapshot pytest; ruff/mypy/tsc green. Also retired a dead backlog
           item (inbox summary as note title — already shipped). No schema/migration,
           no Alembic, no model call, no new dependency.
Sprint 14: [DONE] Security posture hardening — capped inbox capture text at 8,000
           characters for web + Discord (`InboxRawText`) so oversized notes fail
           Pydantic validation before DB writes or model calls; Discord `/inbox`
           followups now use `AllowedMentions.none()` on both success and error
           replies so captured/model text cannot ping roles/users; documented the
           intentional single-user/trusted-LAN posture for `API_HOST=0.0.0.0`; and
           added the reverse-proxy caveat to the loopback Settings write guard.
           Credential rotation, auth, rate limiting, migrations, model/provider
           changes, and new dependencies were out of scope.
Sprint 15: [DONE] UX foundation — converted the frontend to React Router data
           routing (`createBrowserRouter` + `RouterProvider`) so Settings can use
           `useBlocker`; Settings now blocks in-app navigation while profile/prompt
           edits are dirty, alongside the existing browser close/reload guard.
           `AppShell` no longer shows fake focus/sync/disabled topbar affordances
           and instead uses honest local workspace/status copy. `TasksPage` now
           syncs search/status/priority/project/overdue/dueSoon/sort/view/new query
           params both ways so shared links and browser history restore task views.
           Frontend tests were added/updated, but per user request were not run here.
           No backend route, schema/migration, model call, or new dependency.
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

AI evals are opt-in for `test.sh` because they require Ollama and the configured
local model. The default quality gate stays deterministic and does not hide known
frontend flakes by skipping tests.

When `API_HOST=0.0.0.0`, this is intentionally a single-user, trusted-LAN app.
Normal project/task/inbox/trash/training routes are reachable from LAN clients
for both reads and writes. Settings writes remain localhost-only and return
`403` from non-loopback clients, and Discord routes are protected by
`BACKEND_SHARED_SECRET`. This is not multi-user auth; revisit real auth if the
app is exposed beyond a trusted home LAN.

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
