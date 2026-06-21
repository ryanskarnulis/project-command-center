# TODO

Outstanding work for Project Command Center. No sprint numbers — everything below
is **backlog**, grouped by theme. The single exception is **Next sprint** at the
top: the one vertical slice proposed to build next. Everything else is unprioritized
until promoted.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Next sprint (proposed): pick from backlog

The "Today / daily schedule" view shipped (README Sprint 9k / CURRENT.md). No slice is
currently promoted — pick the next strongest themed group from the backlog below when
ready. Candidates: command-bar slash actions, task notes, or recurring task stubs.

---

## Backlog

### UX foundation (cross-cutting)

- [ ] In-app route-change blocking for unsaved Settings edits — the `beforeunload`
      guard shipped, but declarative `<BrowserRouter>` can't use `useBlocker`; needs a
      `createBrowserRouter` conversion (App/AppRoutes/AppShell) before it can warn on
      in-app nav.

### Command Bar / Search

- [ ] **Command-bar slash actions** — extend the now-generic `CommandSearch` input with
      `/done <task>` and `/new <text>`, then AI chat. The bar, debounce, and keyboard
      nav already exist; this is parsing a leading `/` and dispatching to the relevant
      action instead of search.

### Today / Daily Schedule

- [x] **Deterministic daily schedule** — shipped (README Sprint 9k). `/today` route +
      `GET /api/today`: pure Python scheduler bins accepted, not-done tasks into sequential
      time blocks ranked by in-progress→open / due urgency / priority, surfaces overflow
      and blocked tasks separately, and fills idle time even when nothing is formally due.
- [ ] **AI reordering with a "why this order" rationale** — future slice on top of the
      deterministic plan: send the ranked plan through `ai/gateway.py` for an optional
      reorder + brief rationale, still guarded by the Python scheduler (suggestions only).
- [ ] **Calendar-aware scheduling** — schedule around meetings once calendar sync is
      unblocked (currently on the README "do not build" list — revisit when ready).

### Task Comments / Notes

- [ ] **Task notes** — timestamped log entries appended to a task (separate from the
      description). New `task_notes` table (`id`, `task_id FK`, `body`, `created_at`;
      soft-delete via `deleted_at` on the parent task cascade, no own `deleted_at`
      needed). `GET /api/tasks/{id}/notes` + `POST /api/tasks/{id}/notes`; rendered as an
      append-only feed at the bottom of `TaskDetailPage`. Alembic migration required.

### Recurring Tasks

- [ ] **Recurring task stubs** — add a nullable `repeat_interval` field to `tasks`
      (`daily | weekly | monthly | null`; Alembic migration required). When a task with a
      repeat_interval is marked workflow_status=`done`, `services/tasks.py` auto-creates
      the next occurrence with the same title/project/priority/estimate and a `due_date`
      advanced by the interval. New task gets review_status=`accepted` (skips the candidate
      queue). No AI involvement; pure Python in the service layer.

### Features

- [ ] Kanban board over `workflow_status` (`open`/`in_progress`/`done`) — columns +
      drag-to-move updating `workflow_status` via existing `PATCH /api/tasks/{id}`; schema
      already scaffolded (`tasks.workflow_status` + `tasks.estimated_minutes` comment says
      "feeds future kanban"). Global + per-project boards (mirror `/tasks` vs
      `/projects/:id/tasks`); reuse `TaskCard`, respect derived `is_blocked`. FE-heavy;
      likely no new backend route.
- [ ] Calendar view — month/week of tasks by `due_date`, click-through to task detail;
      read-only over existing data (no new schema), replaces the `AppShell` calendar
      placeholder. ⚠️ Confirm scope: "Calendar **sync**" is on the README "do not build
      yet" list (external Google/iCal) — build the *internal, read-only* due-date
      calendar, **not** external sync.

### AI Improvements

- [ ] **Eval regression warning** — after each eval run, compare the new pass rate for
      each suite against the previous run stored in `eval_runs`; surface a red warning
      badge in the Settings Evals section if any suite regressed. Frontend-only; uses the
      existing eval run history endpoint.
- [ ] **Prompt snapshot on save** — when a prompt file is saved via the Settings UI, write
      a timestamped copy to `ai/prompts/.history/<name>.<timestamp>.md` before overwriting.
      Lets you diff before/after a score drop and revert manually. Backend change in the
      prompt-save route; no schema/migration.
- [ ] **AI "break this down"** — per-task action that sends the task's title +
      description through `ai/gateway.py` to suggest subtasks, returned as candidates
      (review_status=`candidate`) for the standard review queue. Reuses the `extract_tasks`
      workflow, eval cases, and training-capture pattern. No new schema beyond what's
      already there.
- [ ] **Surface AI inbox summary as note title** — the extraction response already returns
      a `summary` field (stored on `inbox_items.summary`); use it as the display title in
      the inbox list instead of truncating raw text. Frontend-only change; no
      backend/schema work.

### Discord (follow-ups)

- [ ] `/tasks` command — lists open tasks (optionally filtered to a project) without
      opening the web UI. Calls a new `GET /api/discord/tasks` endpoint (shared-secret
      guarded, same pattern as `/api/discord/inbox`). Bot formats results as a short
      numbered list in the reply.
- [ ] `/done <task search>` command — fuzzy-match a task title from the bot and mark it
      workflow_status=`done`. Backend: `PATCH /api/tasks/{id}` already handles this; add a
      `GET /api/discord/tasks/search?q=` helper for the bot to resolve the title to an ID
      first. If multiple matches, bot replies with a disambiguation list.

### Security

- [ ] Cap input size — `NonBlankStr` (`schemas/common.py`) has `min_length=1` but no
      `max_length`, and Starlette has no default body limit, so `raw_text` on
      `POST /api/inbox` + `/api/discord/inbox` is unbounded → unbounded model prompt + DB
      row. Add a `max_length`. Trivial accidental/malicious DoS.
- [ ] Decide & document the LAN-no-auth posture — with `API_HOST=0.0.0.0`, every
      projects/tasks/inbox/trash/training route is open (read **and** write) on the LAN;
      only Discord (shared secret) + Settings writes (loopback-only) are guarded. Auth is
      on the "do not build yet" list, so this is an accepted risk *if conscious*: document
      the threat model in `README.md`; revisit real auth if exposure widens beyond a
      trusted home LAN.
- [ ] Rotate Discord credentials — `backend/.env` holds a real `DISCORD_BOT_TOKEN` +
      `BACKEND_SHARED_SECRET` (never committed — history is clean — but surfaced in
      review). Regenerate both.
- [ ] Discord reply mention-safety — set `allowed_mentions=AllowedMentions.none()` on the
      `/inbox` followup (`integrations/discord/commands.py`) so a model echo can't
      `@everyone` (defence in depth).
- [ ] Document the loopback-check proxy caveat — `require_local_settings_write`
      (`routes_settings.py:28`) reads `request.client.host` directly; correct for a direct
      bind, but behind a reverse proxy it sees the proxy IP (or is `X-Forwarded-For`-
      bypassable). Add a one-line comment.
- [ ] Rate limiting on model-calling endpoints (`/discord/inbox`, `/projects/{id}/summary`)
      — fine for single-user now; revisit if LAN exposure widens.

### Code Quality

- [ ] De-duplicate training capture — `review_inbox` and `_finalize_inbox` in
      `services/review.py` share ~40 near-identical lines (activity events,
      corrected-output dict, both `record_example` calls). Risky prime-directive-#4 code:
      the paths can silently diverge. Extract `_write_training_examples(db, item, accepted)`
      used by both.
- [ ] Pin backend dependencies — `pyproject.toml` lists `fastapi`/`uvicorn`/`structlog`/etc
      with no version constraints and no lockfile (frontend does this right: caret ranges +
      committed `package-lock.json`). Add a lockfile (`uv` or `pip-tools`) for reproducible
      installs.
- [ ] Minor type/import tidy — import `Callable` from `collections.abc` (not `typing`) in
      `services/settings.py:12`; drop the `# type: ignore[arg-type]` in
      `services/review.py:346` via a `cast` to the `Literal`.
- [!] Flaky `TaskDetailPage.test.tsx` — fails intermittently in the full `npm run test`
      run but passes in isolation (`npm run test -- TaskDetailPage`). Pre-existing test
      pollution / timing under parallel load — likely a leaked timer or unawaited state
      update bleeding across tests.

### Deferred infra

- [ ] `docker-compose.yml` — backend + frontend in containers (deferred: "clean restarts,
      not prod").
- [ ] litestream continuous replication instead of cron backups.

### Nice-to-have

- [ ] Task due-date reminders
- [ ] Dark mode
- [ ] Export tasks to markdown

---

## Custom Model Training *(gated on 200+ `ai_training_examples` rows — the north star)*

- [ ] Export `ai_training_examples` to JSONL training format
- [ ] `training/unsloth/train_task_extractor.py` — Unsloth fine-tune script
- [ ] Evaluate fine-tuned model against eval suite
- [ ] `backend/app/ai/providers/llamacpp.py` — llama.cpp HTTP provider
- [ ] Update `profiles.yaml` to use `llamacpp` provider + new model
- [ ] Regression test: eval suite still passes with custom model
- [ ] Update README: note model swap, new dev commands
