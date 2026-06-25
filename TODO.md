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

### Needs fixing / a decision

- [x] **(med) `TaskUpdate` lets non-nullable fields be cleared to `null`.** `TaskUpdate`
      types `title`, `review_status`, `workflow_status`, and `priority` as `… | None = None`,
      and `update_task` blindly `setattr`s every supplied field. So `PATCH /api/tasks/{id}`
      with `{"title": null}` or `{"priority": null}` pushes `None` into a NOT-NULL column —
      a 500 / invalid domain state instead of a clean **422**. _Verified in
      `schemas/tasks.py:47-57` + `services/tasks.py:464-465` (`setattr` loop)._ **Fix:**
      distinguish *optional-because-omitted* from *nullable-because-clearing-is-allowed*.
      `description`, `due_date`, `assignee_hint`, `parent_task_id`, `estimated_minutes`, and
      `repeat_interval` may legitimately be nulled; `title`, `priority`, `review_status`, and
      `workflow_status` must not. Reject explicit `null` on the latter (a `model_validator`
      keyed on `model_fields_set` keeps omit≠null), returning 422.
- [x] **(med) Explicit `project_id: null` does not actually un-file an accepted task.** The
      PATCH route comment (`routes_tasks.py:284`) says an explicit null "un-files the task,"
      but `update_task` then calls `_default_project_id_for_status` (`services/tasks.py:466`),
      which rehomes any *accepted* task with no project back to General. So for accepted tasks
      the null is silently rehomed, not un-filed — comment and behavior disagree. **Decision:**
      given the "global tasks are always filed" model, keep accepted tasks always filed and
      fix the route comment + any UI language; *or* preserve explicit null as a real unfile.
      Pick one and make the code, comment, and UI agree.
- [x] **(med) `review_inbox` can finalize a partial batch.** The batch path
      (`services/review.py:131-209`) applies whatever decisions are supplied, then sets
      `item.reviewed_at` unconditionally — there's no guard that every live candidate has
      exactly one decision. A partial batch marks the inbox item reviewed while leaving some
      candidate tasks undecided (the one-at-a-time `decide_candidate` path finalizes only when
      no candidate remains, so it's safe). **Fix:** before setting `reviewed_at`, require the
      decision `task_id` set to equal the live-candidate id set exactly — no missing, no
      duplicate — else raise (→ 422). Add a test for the partial/duplicate case.
- [x] **(low/med) `services/tasks.py` raises HTTP errors from domain code.** It imports
      `HTTPException`/`status` (`services/tasks.py:9`) and raises route-shaped 422s for the
      recurrence-requires-due-date rules (`:458`, `:568`, `:607`), weakening the route/service
      boundary the rest of the layer keeps (`TaskCycleError`, `DerivedStatusError` are domain
      exceptions mapped in the route). **Fix:** add a domain exception (e.g.
      `RecurrenceRequiresDueDateError(ValueError)`), raise it from the service, and map it to
      422 in `routes_tasks.py` alongside the existing handlers.
- [x] **(high/docs) `CURRENT.md` contradicts the README's removed-Gantt direction.** README
      records that the planning-view epic (Sprints 17–24, Gantt/calendar) was **removed**
      because it didn't earn its complexity, but `CURRENT.md` still frames the phases epic as
      "now that the Planning view (Gantt/calendar) epic is complete," and Slices 2–3 literally
      build "Phases in the per-project Gantt" and the global `/planning` surface — a Gantt that
      no longer exists. With the product direction shifting toward agent/task orchestration
      rather than generic planning, building phases off this stale framing risks rebuilding a
      surface already cut. **Fix:** rewrite `CURRENT.md` before any phases work — either
      re-scope phases to a planning-free surface (the task list / board) or replace the epic
      to match the new direction. Resolve the disagreement first.
- [ ] **(low, refactor — size before promoting) `TasksPage.tsx` is a god component.**
      `frontend/src/features/tasks/TasksPage.tsx` is ~865 lines doing URL parsing, filters,
      sorting, completed-vs-active data switching, board/list mode, subtask creation, activity
      refresh, recursive rendering, and modal state at once. Not a bug, but past the point where
      a split pays off and a blocker for adding agent-management UI cleanly. **Idea:** extract
      `useTaskUrlState`, `useTaskPageState`, `TaskFilters`, `TaskListView`, `TaskBoardView`, and
      `SubtaskComposer`. Refactor-only (no behavior change); keep diffs reviewable per scope
      discipline, so land it incrementally rather than in one pass.

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
