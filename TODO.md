# TODO

Outstanding work for Project Command Center. No sprint numbers — everything below
is **backlog**, grouped by theme. The single exception is the **current focus**,
which lives in `CURRENT.md` (the Planning view / Gantt slices). Completed work is
archived in `DONE.md`.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Current focus

The **Planning view (Gantt/calendar)** epic is the active work — see `CURRENT.md`.
Slices 1–2 are shipped; the next slice is **Bar-resize to edit estimate**
(slice 3). Everything below is unprioritized backlog until promoted.

---

## Backlog

### Command Bar / Search

- [ ] **Command-bar AI chat** — the third future use of the generic input: route a
      leading natural-language query (or a dedicated verb) through `ai/gateway.py`. The
      slash-command seam (`parseCommand` + ActionRows) is in place to hang this off.

### Today / Daily Schedule

- [ ] **AI reordering with a "why this order" rationale** — future slice on top of the
      deterministic plan: send the ranked plan through `ai/gateway.py` for an optional
      reorder + brief rationale, still guarded by the Python scheduler (suggestions only).
- [ ] **Calendar-aware scheduling** — schedule around meetings once calendar sync is
      unblocked (currently on the README "do not build" list — revisit when ready).

### Features

- [ ] **Project phases** — add first-class project phase/grouping support for
      planning views, including collapse/expand behavior in the Gantt chart and
      phase-level summary bars derived from the earliest child start through the
      latest child due date. Keep this separate from task nesting unless the service
      model says phases should literally be parent tasks.

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

- [ ] Rotate Discord credentials — `backend/.env` holds a real `DISCORD_BOT_TOKEN` +
      `BACKEND_SHARED_SECRET` (never committed — history is clean — but surfaced in
      review). Regenerate both.
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
