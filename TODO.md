# TODO

Outstanding work for Project Command Center. No sprint numbers — everything below
is **backlog**, grouped by theme. The single exception is the **current focus**,
which lives in `CURRENT.md`. Completed work is
archived in `DONE.md`.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Current focus

**Cleaning & hardening — manual review (round 4)** — a static code-read pass over the
update/validation, inbox-review, and service-boundary seams, plus a docs-coherence check.
Findings reproduced against the code on 2026-06-25. _Severity: (high) user-facing breakage ·
(med) bug or confusing state · (low) polish/docs._

### Improvement ideas (nice-to-have — not blockers)
*(How to make these flows more useful / easier to use, gathered during the review. Notes, not
commitments — don't promote without sizing against scope discipline.)*

- **Recurring "checklist" tasks** — the constructive form of the recurrence+subtasks bug:
  when a recurring parent with subtasks completes, clone the whole subtree fresh for the next
  occurrence. That turns recurrence into real multi-step routines ("weekly release checklist")
  instead of single tasks.
- **Show the next occurrence date** next to the repeat badge ("Every week · next Jul 1") so
  the cadence is legible without opening the series timeline.
- **Skip / mark-done a recurrence from the list, Today, and the series view** — today skip
  lives only on the task detail page; surfacing it where the task actually shows up is faster.
- **Restore-with-context on `/trash`** — when restoring a project, offer to also pull back the
  tasks that were rehomed to General on delete (pairs with the restore-asymmetry fix above).
- **Alias UX** — inline "already added" feedback as you type, and optionally surface which
  aliases recently matched an inbox note so their value is visible (feeds match accuracy).
- **Explicit Save + dirty indicator on project/task detail** — a visible "unsaved" dot and/or
  Save button alongside save-on-blur, matching the Settings page, so the save model is obvious
  and refresh-loss is impossible.
- **Bulk select on `/trash`** — checkboxes for multi-restore / multi-purge instead of
  per-card or all-of-a-kind only.


---

## Backlog

*(Feature work — do not promote until the hardening sprint above is closed.)*

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
