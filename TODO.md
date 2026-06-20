# TODO

Incomplete items pulled from the master task list. Organized by urgency/category.

**Status legend:** `[ ]` not started · `[~]` in progress · `[!]` blocked

---

## Sprint 8 — Remaining (in progress)

- [~] Consistent empty / loading / error states across pages
- [ ] Toasts for success / failure
- [~] Shared component layer in `src/components/` — `AppShell` added; reusable inbox capture panel lives in `features/inbox`; primitive Button/Card/Badge still implicit in CSS

---

## Deferred from Sprint 6

- [ ] `docker-compose.yml` — backend + frontend in containers (deferred: "clean restarts, not prod")

---

## Backlog / Nice-to-have

- [ ] litestream continuous replication instead of cron backups
- [ ] Task due-date reminders
- [ ] Dark mode
- [ ] Export tasks to markdown

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
