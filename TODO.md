# TODO

Outstanding work for Project Command Center. No sprint numbers — everything below
is **backlog**, grouped by theme. The single exception is **Next sprint** at the
top: the one vertical slice proposed to build next. Everything else is unprioritized
until promoted.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Next sprint (proposed): pick from backlog

AI "break this down" shipped (README Sprint 10a / DONE.md). No slice is currently
promoted — pick the next strongest themed group from the backlog below when ready.

---

## Backlog

### UX foundation (cross-cutting)

- [ ] In-app route-change blocking for unsaved Settings edits — the `beforeunload`
      guard shipped, but declarative `<BrowserRouter>` can't use `useBlocker`; needs a
      `createBrowserRouter` conversion (App/AppRoutes/AppShell) before it can warn on
      in-app nav.
- [ ] **Shell truthfulness pass** — `AppShell` still has optimistic/static shell chrome:
      "Focus mode On", disabled topbar notification/search/customize buttons, and
      "Last synced just now" in a local-first app with no sync. Either make each affordance
      real or replace it with honest local workspace/status copy.
- [ ] **Task filter URL sync** — `TasksPage` seeds filters/sort from query params, but
      edits stay local after mount. Push filter/sort changes back into the URL so dashboard
      links, browser back/forward, and shared views stay stable.

### Command Bar / Search

- [x] **Command-bar slash actions** — shipped (README Sprint 9m / DONE.md). `/new <text>`
      (capture → extract → note-review) and `/done <task>` (fuzzy-find → complete via the
      recurrence-preserving done endpoint) via a pure `parseCommand` parser and unified
      action rows. `SearchResultItem` gained `review_status`/`workflow_status` (no
      migration) so `/done` offers only accepted, not-done tasks.
- [ ] **Command-bar AI chat** — the third future use of the generic input: route a
      leading natural-language query (or a dedicated verb) through `ai/gateway.py`. The
      slash-command seam (`parseCommand` + ActionRows) is in place to hang this off.
- [x] **Command-bar focus shortcut** — shipped (README Sprint 9o). Global `Cmd/Ctrl+K`
      window keydown listener focuses + selects the bar and opens the dropdown
      (preventDefault on the browser's own binding); Escape still blurs. Vitest covers
      the mac/non-mac shortcut and the bare-`k` no-op.
- [x] **Search relevance pass** — shipped (README Sprint 9o). Per-kind SQLAlchemy
      `case()` relevance score orders exact > prefix > substring > description/raw-text
      matches (id desc tiebreak); tasks use a separate state tie-breaker so accepted +
      not-done work outranks done/candidate noise at the same text tier. Pure SQL/Python,
      no model call, no schema change; `SearchResults` shape unchanged. pytest ordering
      tests added.

### Today / Daily Schedule

- [x] **Deterministic daily schedule** — shipped (README Sprint 9k). `/today` route +
      `GET /api/today`: pure Python scheduler bins accepted, not-done tasks into sequential
      time blocks ranked by in-progress→open / due urgency / priority, surfaces overflow
      and blocked tasks separately, and fills idle time even when nothing is formally due.
- [x] **Today quick actions** — shipped (README Sprint 9n / DONE.md). In-row Mark done
      (`POST /api/tasks/{id}/done`, recurrence-safe) + Start (`PATCH workflow_status=in_progress`)
      on scheduled + overflow rows; in-progress rows hide Start; each action refetches the plan
      (`useTodayPlan().refetch`) with per-row pending state and `useToast` errors. Frontend-only.
- [x] **Blocked dependency clarity** — shipped (README Sprint 9n / DONE.md). `BlockedTask`
      now carries `blocking_tasks` (title + workflow_status, replacing bare `blocking_task_ids`)
      from the existing dependency loop (no new query, no migration); `/today` blocked rows
      render each blocker's title + status pill linking to `/tasks/:id`.
- [ ] **AI reordering with a "why this order" rationale** — future slice on top of the
      deterministic plan: send the ranked plan through `ai/gateway.py` for an optional
      reorder + brief rationale, still guarded by the Python scheduler (suggestions only).
- [ ] **Calendar-aware scheduling** — schedule around meetings once calendar sync is
      unblocked (currently on the README "do not build" list — revisit when ready).

### Recurring Tasks

- [x] **Recurring task stubs** — shipped (README Sprint 9L / DONE.md). `tasks.repeat_interval`
      (JSON `{unit, every}`) + `tasks.recurrence_id` (series chain); marking a recurring task
      done auto-creates the next occurrence (due date advanced, month math day-clamped),
      `skip_recurrence` suppresses it, `edit_scope="future"` forward-patches the series.
      `repeat_interval` requires a `due_date` (422). Frontend RepeatIntervalInput +
      EditScopeModal + skip button + TaskCard repeat badge. Pure Python service layer, no AI.
- [x] **Recurring series management** — shipped (README Sprint 12). `GET /api/tasks/{id}/series`
      returns every occurrence sharing a `recurrence_id` (incl. soft-deleted skipped rows,
      oldest first) and `POST /api/tasks/{id}/stop-recurrence` clears `repeat_interval` while
      keeping the chain id. Lazy-loaded RecurrenceSeries timeline + confirm-gated Stop
      recurrence on TaskDetailPage. Future edits already shipped via `edit_scope` (9L). No
      migration, no model call, no new dependency.

### Features

- [x] Kanban board over `workflow_status` (`open`/`in_progress`/`done`) — shipped
      (README Sprint 11). `?view=board` toggle on `TasksPage` (global `/tasks` +
      per-project `/projects/:id/tasks`); `KanbanBoard` flat-card columns reusing
      `TaskCard`. Native HTML5 drag + a per-card "Move to" `<select>` (keyboard/a11y).
      Done column sourced from the completed archive (`useCompletedTasks`). Moves route
      to the correct endpoint: into Done → recurrence-safe `POST /done`, out of Done →
      `reopen` (→ open, then PATCH if In progress), else `PATCH workflow_status`. Refuses
      moving a derived-`is_blocked` task into In progress/Done (toast). Frontend-only:
      no new backend route, no schema/migration, no model call, no new dependency.
- [x] Calendar view — shipped (README Sprint 10b). Internal read-only month/week
      calendar of tasks by `due_date` at `/calendar` (`GET /api/calendar?start=&end=`,
      new `services/calendar.py` — accepted tasks incl. done, candidate/deleted
      excluded; flat `list[TaskRead]` reusing `_reads_with_blocked`). Reached only via
      the dashboard "Upcoming Events" tile (now real: soonest-due tasks + a real
      **View calendar** link), not the global nav. No schema/migration, no model call,
      no new dependency. NOT external Google/iCal sync (stays on the do-not-build list).

### AI Improvements

- [ ] **Eval regression warning** — after each eval run, compare the new pass rate for
      each suite against the previous run stored in `eval_runs`; surface a red warning
      badge in the Settings Evals section if any suite regressed. Frontend-only; uses the
      existing eval run history endpoint.
- [ ] **Prompt snapshot on save** — when a prompt file is saved via the Settings UI, write
      a timestamped copy to `ai/prompts/.history/<name>.<timestamp>.md` before overwriting.
      Lets you diff before/after a score drop and revert manually. Backend change in the
      prompt-save route; no schema/migration.
- [x] **AI "break this down"** — shipped (README Sprint 10a / DONE.md). Per-task
      `POST /api/tasks/{id}/break-down` runs a new `break_down_task` workflow (gateway →
      Pydantic `BreakdownOutput` → candidate children via `create_task(parent_task_id=...)`),
      reviewed on TaskDetailPage (Approve/Dismiss) via `POST /api/tasks/{id}/breakdown/review`,
      which captures one training correction row. New `break_down_task` profile/prompt/eval
      suite (6/6 on gemma4:e2b). One nullable `tasks.breakdown_output_json` column +
      migration (needed to hold the original output between generate- and review-time for
      directive #4 — the backlog's "no new schema" hope was not achievable).
- [ ] **Surface AI inbox summary as note title** — the extraction response already returns
      a `summary` field (stored on `inbox_items.summary`); use it as the display title in
      the inbox list instead of truncating raw text. Frontend-only change; no
      backend/schema work.
- [ ] **Training corpus QA filters** — the Training page computes corrected / accepted /
      extraction-failure status, but the filter only exposes accepted vs rejected. Add
      derived status, model/profile, and "corrected only" filters before the custom-model
      export phase so corpus cleanup is easier.

### Discord (follow-ups)

- [ ] `/tasks` command — lists open tasks (optionally filtered to a project) without
      opening the web UI. Calls a new `GET /api/discord/tasks` endpoint (shared-secret
      guarded, same pattern as `/api/discord/inbox`). Bot formats results as a short
      numbered list in the reply.
- [ ] `/done <task search>` command — fuzzy-match a task title from the bot and mark it
      workflow_status=`done`. Backend: use the recurrence-preserving
      `POST /api/tasks/{id}/done` endpoint after resolving the task; add a
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
