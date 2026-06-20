# TODO

Incomplete items pulled from the master task list. Organized by urgency/category.

**Status legend:** `[ ]` not started · `[~]` in progress · `[!]` blocked

---

## Sprint 8 — Remaining (in progress)

- [~] Consistent empty / loading / error states across pages
- [ ] Toasts for success / failure
- [~] Shared component layer in `src/components/` — `AppShell` added; reusable inbox capture panel lives in `features/inbox`; primitive Button/Card/Badge still implicit in CSS

---

## Sprint 9g — Remaining (follow-ups)

- [ ] In-app route-change blocking for unsaved Settings edits — chunk 2 shipped the
      `beforeunload` guard, but declarative `<BrowserRouter>` can't use `useBlocker`; needs a
      `createBrowserRouter` conversion (App/AppRoutes/AppShell) before it can warn on in-app nav.
- [!] Flaky `TaskDetailPage.test.tsx` — fails intermittently in the full `npm run test` run but
      passes in isolation (`npm run test -- TaskDetailPage`). Pre-existing test pollution / timing
      under parallel load, unrelated to the Sprint 9g settings work — likely a leaked timer or
      unawaited state update bleeding across tests.

---

## Deferred from Sprint 6

- [ ] `docker-compose.yml` — backend + frontend in containers (deferred: "clean restarts, not prod")

---

## Backlog / Nice-to-have

- [ ] litestream continuous replication instead of cron backups
- [ ] Task due-date reminders
- [ ] Dark mode
- [ ] Export tasks to markdown

### Command Bar / Search

- [ ] **Global search** — wire the existing AppShell search placeholder to a `GET /api/search?q=`
      route that queries projects, tasks, and inbox with a simple `LIKE`; return typed results
      grouped by kind; keyboard-navigate and click-through to the relevant page. No new schema.
      This is the first slice; the bar is designed to grow into an AI chat + slash-command surface
      (e.g. `/done <task>`, `/new <text>`) in later sprints, so keep the input component generic
      from the start.

### Today / Daily Schedule

- [ ] **AI-generated daily schedule** — a `/today` route (or dashboard section) that does more than
      filter due-today tasks: builds a prioritized schedule for the day using open tasks, their
      estimates, priorities, and due dates, filling idle time even when nothing is formally due.
      Initial version: pure Python scheduling logic (no model call needed) that bins tasks into
      time blocks and surfaces them in order. Future slices: (1) AI reordering with a brief
      "why this order" rationale, (2) calendar integration to schedule around meetings once
      calendar sync is unblocked (currently on the README "do not build" list — revisit when ready).

### Task Comments / Notes

- [ ] **Task notes** — timestamped log entries appended to a task (separate from the description).
      New `task_notes` table (`id`, `task_id FK`, `body`, `created_at`; soft-delete via `deleted_at`
      on the parent task cascade, no own `deleted_at` needed). `GET /api/tasks/{id}/notes` +
      `POST /api/tasks/{id}/notes`; rendered as an append-only feed at the bottom of
      `TaskDetailPage`. Alembic migration required.

### Recurring Tasks

- [ ] **Recurring task stubs** — add a nullable `repeat_interval` field to `tasks`
      (`daily | weekly | monthly | null`; Alembic migration required). When a task with a
      repeat_interval is marked workflow_status=`done`, `services/tasks.py` auto-creates the next
      occurrence with the same title/project/priority/estimate and a `due_date` advanced by the
      interval. New task gets review_status=`accepted` (skips the candidate queue). No AI
      involvement; pure Python in the service layer.

### Features

- [ ] Kanban board over `workflow_status` (`open`/`in_progress`/`done`) — columns + drag-to-move
      updating `workflow_status` via existing `PATCH /api/tasks/{id}`; schema already scaffolded
      (`tasks.workflow_status` + `tasks.estimated_minutes` comment says "feeds future kanban").
      Global + per-project boards (mirror `/tasks` vs `/projects/:id/tasks`); reuse `TaskCard`,
      respect derived `is_blocked`. FE-heavy; likely no new backend route.
- [ ] Calendar view — month/week of tasks by `due_date`, click-through to task detail; read-only
      over existing data (no new schema), replaces the `AppShell` calendar placeholder.
      ⚠️ Confirm scope: "Calendar **sync**" is on the README "do not build yet" list (external
      Google/iCal) — build the *internal, read-only* due-date calendar, **not** external sync.

---

## AI Improvements

- [ ] **Eval regression warning** — after each eval run, compare the new pass rate for each suite
      against the previous run stored in `eval_runs`; surface a red warning badge in the Settings
      Evals section if any suite regressed. Frontend-only; uses the existing eval run history
      endpoint.
- [ ] **Prompt snapshot on save** — when a prompt file is saved via the Settings UI, write a
      timestamped copy to `ai/prompts/.history/<name>.<timestamp>.md` before overwriting. Lets you
      diff before/after a score drop and revert manually. Backend change in the prompt-save route;
      no schema/migration.
- [ ] **Sprint 11 — AI "break this down"** — per-task action that sends the task's title +
      description through `ai/gateway.py` to suggest subtasks, returned as candidates
      (review_status=`candidate`) for the standard review queue. Reuses the `extract_tasks`
      workflow, eval cases, and training-capture pattern. No new schema beyond what's already
      there. (Named in README backlog; formalizing here.)
- [ ] **Surface AI inbox summary as note title** — the extraction response already returns a
      `summary` field (stored on `inbox_items.summary`); use it as the display title in the inbox
      list instead of truncating raw text. Frontend-only change; no backend/schema work.

---

## Discord (follow-ups to Sprint 3)

- [ ] `/tasks` command — lists open tasks (optionally filtered to a project) without opening the
      web UI. Calls a new `GET /api/discord/tasks` endpoint (shared-secret guarded, same pattern
      as `/api/discord/inbox`). Bot formats results as a short numbered list in the reply.
- [ ] `/done <task search>` command — fuzzy-match a task title from the bot and mark it
      workflow_status=`done`. Backend: `PATCH /api/tasks/{id}` already handles this; add a
      `GET /api/discord/tasks/search?q=` helper for the bot to resolve the title to an ID first.
      If multiple matches, bot replies with a disambiguation list.

---

## Security

- [ ] Decide & document the LAN-no-auth posture — with `API_HOST=0.0.0.0`, every
      projects/tasks/inbox/trash/training route is open (read **and** write) on the LAN; only
      Discord (shared secret) + Settings writes (loopback-only) are guarded. Auth is on the "do
      not build yet" list, so this is an accepted risk *if conscious*: document the threat model
      in `README.md`; revisit real auth if exposure widens beyond a trusted home LAN.
- [ ] Cap input size — `NonBlankStr` (`schemas/common.py`) has `min_length=1` but no `max_length`,
      and Starlette has no default body limit, so `raw_text` on `POST /api/inbox` + `/api/discord/inbox`
      is unbounded → unbounded model prompt + DB row. Add a `max_length`. Trivial accidental/malicious DoS.
- [ ] Rotate Discord credentials — `backend/.env` holds a real `DISCORD_BOT_TOKEN` +
      `BACKEND_SHARED_SECRET` (never committed — history is clean — but surfaced in the review). Regenerate both.
- [ ] Discord reply mention-safety — set `allowed_mentions=AllowedMentions.none()` on the `/inbox`
      followup (`integrations/discord/commands.py`) so a model echo can't `@everyone` (defence in depth).
- [ ] Document the loopback-check proxy caveat — `require_local_settings_write`
      (`routes_settings.py:28`) reads `request.client.host` directly; correct for a direct bind, but
      behind a reverse proxy it sees the proxy IP (or is `X-Forwarded-For`-bypassable). Add a one-line comment.
- [ ] Rate limiting on model-calling endpoints (`/discord/inbox`, `/projects/{id}/summary`) — fine
      for single-user now; revisit if LAN exposure widens.

---

## Code Quality

- [ ] De-duplicate training capture — `review_inbox` and `_finalize_inbox` in `services/review.py`
      share ~40 near-identical lines (activity events, corrected-output dict, both `record_example`
      calls). Risky prime-directive-#4 code: the paths can silently diverge. Extract
      `_write_training_examples(db, item, accepted)` used by both.
- [ ] Pin backend dependencies — `pyproject.toml` lists `fastapi`/`uvicorn`/`structlog`/etc with no
      version constraints and no lockfile (frontend does this right: caret ranges + committed
      `package-lock.json`). Add a lockfile (`uv` or `pip-tools`) for reproducible installs.
- [ ] Minor type/import tidy — import `Callable` from `collections.abc` (not `typing`) in
      `services/settings.py:12`; drop the `# type: ignore[arg-type]` in `services/review.py:346` via
      a `cast` to the `Literal`.

---

## Custom Model Training *(gated on 200+ `ai_training_examples` rows)*

- [ ] Export `ai_training_examples` to JSONL training format
- [ ] `training/unsloth/train_task_extractor.py` — Unsloth fine-tune script
- [ ] Evaluate fine-tuned model against eval suite
- [ ] `backend/app/ai/providers/llamacpp.py` — llama.cpp HTTP provider
- [ ] Update `profiles.yaml` to use `llamacpp` provider + new model
- [ ] Regression test: eval suite still passes with custom model
- [ ] Update README: note model swap, new dev commands
