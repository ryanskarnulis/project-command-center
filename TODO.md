# TODO

Outstanding work for Project Command Center. No sprint numbers — everything below
is **backlog**, grouped by theme. The single exception is the **current focus**,
which lives in `CURRENT.md`. Completed work is
archived in `DONE.md`.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Current focus

**Cleaning & hardening — manual review (round 3)** — the deeper pass the round-2 note
deferred: Trash restore/purge round-trips, recurrence series actions, alias CRUD, and the
project-description save flow. Done 2026-06-24 with code review + service-layer scripts +
headless-chromium browser drives + the full backend suite (289 passing). Each finding below
was reproduced, not just read. _Severity: (high) user-facing breakage · (med) bug or
confusing state · (low) polish/docs._

### Needs fixing / a decision

- [x] **(med) Recurrence + subtasks silently kills the series.** _Resolved via the
      "recurring checklist" approach: completing the last child now spawns a fresh clone
      of the whole subtree as the next occurrence (`_maybe_spawn_recurring_checklist` +
      subtree-aware `_create_next_occurrence` in `services/tasks.py`). The parent stays
      derived/read-only; the series advances from the child-completion path._ A task with
      `repeat_interval` set *and* active subtasks has a derived (read-only) workflow status,
      so it can never make the stored `open→done` transition that `mark_done` /
      `update_task` use to spawn the next occurrence. Completing all children rolls the parent
      up to "done" (derived) but `_create_next_occurrence` never fires, and a direct
      mark-done is refused with `DerivedStatusError`. Net: the cadence dies, `repeat_interval`
      sits inert, and the parent reads as done-but-recurring. _Verified via service script
      (`backend/app/services/tasks.py`)._ **Decision:** either refuse recurrence on a task
      that has (or gains) children, or spawn the next occurrence when a recurring parent rolls
      up to done (see the "recurring checklist" idea below for the richer version).
- [x] **(med) Restoring a skipped occurrence duplicates the live series.** _Resolved via
      "un-skip on restore": `restore_task` is now recurrence-aware — when the restored row
      belongs to a series with a live forward occurrence (earliest active sibling due on/after
      it), it pulls that occurrence's date (and its whole subtree's) back to the restored date
      via `_reschedule_occurrence`, then hard-deletes the restored row with `purge_task`. Net:
      the series resumes at the un-skipped date with exactly one live occurrence — no duplicate.
      Non-recurring rows and series with no live forward occurrence fall through to the plain
      restore (rehome-to-General + clear `deleted_at`). Tests in `test_recurrence.py` (leaf,
      checklist, both fallbacks); 298 backend passing._ Skip soft-deletes
      occurrence *N* and spawns *N+1*. The skipped row still appears in `/trash` as an
      ordinary task; restoring it leaves **two** active occurrences in the same series, both
      carrying `repeat_interval` — and completing the restored one spawns yet another
      duplicate. _Verified via service script + confirmed the skipped row is offered as
      restorable._ Trash restore is recurrence-unaware.
- [x] **(med) Restoring a project gives back an empty project.** _Resolved by making
      delete/restore symmetric and project-scoped: deleting a project now **cascade-soft-deletes
      its tasks (and subtrees) with it** (stamped `tasks.deleted_with_project_id`, migration
      `5be1ff02ca06`) instead of rehoming to General; restoring asks whether to bring those tasks
      back (`restore_project(restore_tasks=...)` → `POST /api/projects/{id}/restore?restore_tasks=`).
      Tasks trashed independently keep a null marker and aren't swept back; cascade tasks don't
      appear as standalone `/trash` rows. `/trash` project cards show `archived_task_count` and a
      confirm-gated "bring back N tasks" restore. Replaces the old rehome-to-General behavior
      (README updated). Backend + frontend tests; 302 backend / 211 frontend passing._ Deleting
      a project rehomed its active tasks to General; restore only cleared `deleted_at`, so the
      project came back empty and asymmetric.
- [x] **(low/med) Duplicate & case-variant aliases are accepted.** _Resolved with a
      normalized dedupe guard backed by the DB: `project_aliases` gains a `normalized_alias`
      column (= `_normalize(alias)`, shared with the matcher) and a partial unique index over
      active rows (`uq_project_alias_normalized`, migration `7ebcc24824c9`). `create_alias`
      raises `DuplicateAliasError` → the route returns **409**; the frontend pre-disables the
      **Add** button and shows an "already added" hint when the typed value normalizes to an
      existing alias. Soft-delete + re-add still works (active-only index). Tests in
      `test_projects.py`; 306 backend passing._
      `projects.create_alias` had no uniqueness or normalization guard (no DB constraint on
      `project_aliases` either); adding `fw`, `fw`, `FW` yielded three rows.
- [x] **(low) No unsaved-changes guard on ProjectDetailPage / TaskDetailPage.** _Resolved by
      adding a `beforeunload` guard to both pages via a shared `useBeforeUnload(dirty)` hook
      (`frontend/src/hooks/useBeforeUnload.ts`, extracted from SettingsPage's inlined effect).
      `dirty` = the active draft differs from the loaded server values. Scoped to refresh/close
      only — in-app nav stays covered by save-on-blur, so no `useBlocker`/discard-modal was added
      here. SettingsPage now reuses the same hook. Tests in both detail suites spy on
      add/removeEventListener; 23 passing across the two pages + Settings._ Both save-on-blur, so
      in-app navigation is safe — clicking a `<Link>` blurs the field first and the edit persists.
      But a **refresh / tab-close** with a focused, edited-but-not-blurred field silently discarded
      the edit, with no `beforeunload` prompt — unlike SettingsPage (Sprint 15).
- [x] **(low) Stale "FK enforcement is off on SQLite" comments.** _Fixed: the two comments
      that carried the wrong rationale (`common.hard_delete`,
      `tasks._deleted_subtree_depth_first`) now state it accurately — FK enforcement **is** on
      (`PRAGMA foreign_keys = ON`, `db/session.py`), but SQLite FKs don't auto-cascade, so a
      missed edge would *raise* and manual cleanup is still required. (`tasks.purge_task` /
      `projects.purge_project` referenced FK cleanup without the false claim, so they were left
      as-is.)_ `db/session.py:45` issues `PRAGMA foreign_keys = ON` in prod (and conftest enables
      it in tests), but the comments claimed FK enforcement is off — backwards.
- [x] **(low) `POST /api/tasks` silently ignores a supplied `project_id`.** _Resolved by
      **honoring** `project_id`: `TaskCreate` gains a `project_id: int | None = None` field and
      `create_unscoped_task` passes it through, validating a non-null value with `_ensure_project`
      (404 on a bad id). The project-scoped route keeps the **path** id authoritative and ignores
      the body field. Omitting it preserves the file-in-General default. Tests in `test_tasks.py`
      (honored / 404 / General regression); 308 backend passing._ `create_unscoped_task`
      hard-coded `project_id=None` and `TaskCreate` had no field, so `{project_id: N}` filed in
      General silently.

**Verified clean (no action):** recurrence detail UI is correct — repeat badge, "Skip this
occurrence" (with confirm), lazy `RecurrenceSeries` timeline (including skipped rows), and the
`EditScopeModal` all fire (changing a series field prompts this/future scope); `stop-recurrence`
and skip non-recurring → 422 hold; month-clamp math is right. Trash purge round-trips are solid
— purge guards on already-trashed (409 on active, 403 on General), cleans dependency/alias/
subtree/nullable-FK edges, keeps `ai_training_examples`, and is idempotent; the empty-trash and
per-card purge buttons confirm first. Alias add/remove and the description save-on-blur both
persist. Backend suite 289/289 green. _(Frontend Vitest not run this pass — `TaskDetailPage` /
`ProjectDetailPage` suites are known-flaky on a clean tree; see memory.)_

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
