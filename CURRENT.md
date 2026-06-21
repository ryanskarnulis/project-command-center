# Current Sprint

## Next Sprint — Recurring Task Stubs

Add optional recurrence to tasks so that when a recurring task is marked
done, the next occurrence is automatically created with the due date
advanced by the configured interval.

**Scope guard:** pure Python service layer, no AI involvement, no calendar
sync. Each recurrence is an independent task row. The repeat field uses the
same natural-text input pattern as `estimated_minutes`. A `recurrence_id`
UUID chains the series so "edit all future" and "skip" can target the right
rows. No `/today` scheduler changes in v1.

---

### Design decisions (settled before planning)

**Interval format**
Natural text input mirroring the estimate field: `daily`, `weekly`,
`every 2 weeks` … `every 12 weeks`, `monthly`, `every 2 months` …
`every 12 months`. Stored as a JSON column `repeat_interval` on `tasks`:
`{ "unit": "day" | "week" | "month", "every": 1–12 }`. JSON avoids integer
drift on month math. `null` means non-recurring.

**Due date inheritance**
Next occurrence inherits the *original* due date offset — e.g. a weekly
task due Monday always lands on the next Monday, regardless of when the
user actually marks it done.

**Require due date**
`repeat_interval` is only valid when `due_date` is set. The backend rejects
a `repeat_interval` with no `due_date` (422). The UI disables the repeat
field when due date is blank and shows a tooltip explaining why.

**Skip mechanism**
"Skip this occurrence" button on `TaskDetailPage`. Marks the current task
`workflow_status=done` but passes a `skip_recurrence=true` flag to the
complete endpoint, suppressing next-occurrence creation.

**`recurrence_id`**
A `UUID` column on `tasks`, nullable. Set on the first task in the series
when `repeat_interval` is first saved, and copied to every auto-created
occurrence. Allows "edit all future" to `UPDATE tasks SET ... WHERE
recurrence_id = ? AND due_date >= ?` and "skip" to work without a separate
join table.

**Edit scope**
- Editing `repeat_interval`, `title`, `description`, `priority`, or
  `estimated_minutes` on a recurring task offers a choice: **This task
  only** or **This and all future occurrences**.
- "All future" patches rows with the same `recurrence_id` and
  `due_date >= this task's due_date` (already-done occurrences are left
  alone).
- For `due_date` edits, only "this task" makes sense (each occurrence has
  its own date). The UI skips the prompt for due-date-only edits.

**New occurrence on completion**
When `workflow_status` is set to `done` on a task with a non-null
`repeat_interval` (and `skip_recurrence` is not set), `services/tasks.py`
auto-creates the next occurrence:
- Same `title`, `description`, `priority`, `estimated_minutes`,
  `repeat_interval`, `recurrence_id`, `project_id`.
- `due_date` = current task's `due_date` + interval.
- `review_status = accepted` (skips candidate queue).
- `workflow_status = open`.
- `parent_task_id = null` (occurrences are top-level).

**No occurrence on reopen**
Reopening a done recurring task does not delete the already-created next
occurrence. The user manages any duplicate manually.

---

### Schema changes (Alembic migration required)

On `tasks`:
- `repeat_interval`: `JSON | NULL` — `{ "unit": "day"|"week"|"month", "every": int }`
- `recurrence_id`: `UUID (char(36)) | NULL` — shared across a series
- No new table.

---

### API changes

**`PATCH /api/tasks/{id}`** gains:
- `repeat_interval: { unit, every } | null` — set/clear recurrence.
- `skip_recurrence: bool = false` — when `workflow_status=done` is patched
  in the same request, suppresses next-occurrence creation.
- `edit_scope: "this" | "future" = "this"` — when present with any other
  field change on a recurring task, applies the edit forward.

**New occurrence creation** is internal to `services/tasks.py`; no new
route.

---

### Files in play

Backend:
- `backend/app/db/models.py` — add `repeat_interval` + `recurrence_id` to `Task`.
- `backend/alembic/versions/<hash>_add_recurrence_fields.py` — migration.
- `backend/app/schemas/tasks.py` — `RepeatInterval` model, update `TaskUpdate` + `TaskRead`.
- `backend/app/services/tasks.py` — recurrence logic in `update_task`: next-occurrence
  creation, skip flag, edit-scope forward-patch.
- `backend/tests/test_recurrence.py` — recurrence behavior tests.

Frontend:
- `frontend/src/types/tasks.ts` — add `repeat_interval`, `recurrence_id` to `Task`.
- `frontend/src/features/tasks/RepeatIntervalInput.tsx` — natural-text input
  component (mirrors `EstimateInput`).
- `frontend/src/features/tasks/EditScopeModal.tsx` — "This task only / This
  and all future" prompt, shown before applying edits to recurring tasks.
- `frontend/src/features/tasks/TaskDetailPage.tsx` — repeat field, skip button,
  edit-scope prompt wiring.
- `frontend/src/features/tasks/TaskCard.tsx` — small repeat badge.
- `frontend/src/utils/recurrence.ts` — `parseRepeatInterval(text)` +
  `formatRepeatInterval(interval)`.
- `frontend/src/index.css` — repeat badge style on existing tokens.

---

### Chunk plan

---

**Chunk A — DB model + Alembic migration**

- Add `repeat_interval: Mapped[dict | None]` (JSON) and
  `recurrence_id: Mapped[str | None]` (char(36)) to `Task` in `models.py`.
- `alembic revision --autogenerate -m "add recurrence fields"`.
- Review generated file; apply `alembic upgrade head`.
- No service, schema, or frontend changes.

Verification: `alembic upgrade head` clean; `.schema tasks` shows both columns.

---

**Chunk B — Pydantic schemas**

- Add `RepeatInterval(BaseModel)`: `unit: Literal["day","week","month"]`,
  `every: int` (ge=1, le=12). Must always emit both fields (no defaults —
  see project memory on required-nullable fields).
- Update `TaskRead`: add `repeat_interval: RepeatInterval | None`,
  `recurrence_id: str | None`.
- Update `TaskUpdate`: add `repeat_interval: RepeatInterval | None = UNSET`,
  `skip_recurrence: bool = False`,
  `edit_scope: Literal["this","future"] = "this"`.
- Validator on `TaskUpdate`: if `repeat_interval` is set and `due_date` is
  not provided, reject with a clear message (422). If the task being updated
  already has a `due_date` this check relaxes — the validator can't see DB
  state, so the service layer re-checks and raises `HTTPException(422)`.
- `mypy --strict` + `ruff check` clean.

Verification: schema unit tests (inline doctest or small pytest) confirm
`RepeatInterval` serializes correctly and rejects bad `every` values.

---

**Chunk C — Service layer recurrence logic**

All changes in `services/tasks.py`:

1. **`_next_due_date(due_date, interval) -> date`** — pure function:
   - `day`: `due_date + timedelta(days=every)`
   - `week`: `due_date + timedelta(weeks=every)`
   - `month`: `dateutil.relativedelta` if already a dep; otherwise manual
     month math with day-clamping. Check `pyproject.toml` first — do not
     add `python-dateutil` without asking.
2. **`_create_next_occurrence(db, task) -> Task`** — clones the completed
   task row, advances `due_date`, copies `recurrence_id`.
3. **`update_task` changes:**
   - If `workflow_status=done` and task has `repeat_interval` and not
     `skip_recurrence`: call `_create_next_occurrence`.
   - If `edit_scope="future"` and task has `recurrence_id`: bulk-patch
     rows with same `recurrence_id` and `due_date >= task.due_date`
     for the changed fields (exclude `due_date`, `workflow_status`,
     `skip_recurrence`, `edit_scope` from the forward patch).
   - If `repeat_interval` is being set for the first time (was null):
     generate and assign a new `recurrence_id` UUID.
   - If `repeat_interval` is being cleared: leave `recurrence_id` as-is
     (the history chain stays readable) but stop generating occurrences.

Verification: `pytest tests/test_recurrence.py` — write tests in this chunk:
- Complete non-recurring task → no new task created.
- Complete recurring task → next occurrence has correct due date, same
  `recurrence_id`, `review_status=accepted`, `workflow_status=open`.
- Complete with `skip_recurrence=true` → no next occurrence.
- `edit_scope="future"` → patches forward rows, leaves past rows alone.
- `edit_scope="this"` → patches only the target row.
- Setting `repeat_interval` on a task with no `due_date` → 422.
- Month interval: Jan 31 + 1 month → Feb 28 (not a crash).
- Setting `repeat_interval` first time → `recurrence_id` populated.
- Clearing `repeat_interval` → no new occurrence on next complete.

---

**Chunk D — Frontend utilities + RepeatIntervalInput**

- `frontend/src/utils/recurrence.ts`:
  - `parseRepeatInterval(text: string): RepeatInterval | null` — parses
    `"daily"`, `"weekly"`, `"every 2 weeks"`, `"monthly"`,
    `"every 3 months"`. Returns `null` on unrecognized input.
  - `formatRepeatInterval(interval: RepeatInterval): string` — inverse.
  - Unit tests inline or in a `.test.ts` file.
- `frontend/src/features/tasks/RepeatIntervalInput.tsx`:
  - Text input with the same interaction as `EstimateInput` (type, blur
    to parse, show formatted value, show error on unrecognized).
  - Disabled with tooltip `"Set a due date to enable recurrence"` when
    `due_date` is blank.
  - Shows `null` as empty / placeholder `"e.g. weekly, every 2 months"`.

Verification: `tsc --noEmit` clean; parse/format round-trips correct.

---

**Chunk E — TaskDetailPage wiring + EditScopeModal**

- `frontend/src/features/tasks/EditScopeModal.tsx`:
  - Simple modal: "Apply to this task only" / "Apply to this and all future
    occurrences". Reuses existing `Modal` component.
  - Shown when the user saves any field change on a task where
    `recurrence_id` is non-null and `edit_scope` matters (not for
    `due_date`-only edits).
- `TaskDetailPage.tsx` changes:
  - Add repeat field row (uses `RepeatIntervalInput`), positioned after due
    date.
  - On save of any field, if task is recurring, intercept → show
    `EditScopeModal` → pass chosen `edit_scope` to the `PATCH` call.
  - Add "Skip this occurrence" button in the task actions area (near the
    workflow status control). Calls `PATCH` with
    `{ workflow_status: "done", skip_recurrence: true }`. Confirm with a
    short inline prompt before firing ("Skip and mark done — the next
    occurrence won't be created. Continue?").
- `TaskCard.tsx`: add a small repeat badge (e.g. a refresh icon + interval
  label) when `repeat_interval` is non-null, alongside the estimate badge.
- `frontend/src/index.css`: repeat badge styles on existing tokens.

Verification: open a recurring task in the browser —
1. Edit title → EditScopeModal appears → choose "future" → confirm forward
   rows updated.
2. Mark done → next occurrence appears in task list with correct due date.
3. Skip → no next occurrence.
4. Repeat field disabled when due date is blank.

---

**Chunk F — Tests + docs**

- Frontend tests (in `TaskDetailPage.test.tsx` or a new
  `Recurrence.test.tsx`):
  - Repeat field renders and is disabled without a due date.
  - EditScopeModal appears on save for a recurring task.
  - Skip button calls PATCH with `skip_recurrence: true`.
- `npm run test -- Recurrence` (or the relevant file) must pass.
- Update `README.md`: add Sprint 9L (or next letter) entry marking
  recurring task stubs shipped.
- Update `TODO.md`: mark recurring tasks done; leave command-bar slash
  actions and eval regression warning as next candidates.

Verification: `pytest tests/test_recurrence.py` still green; `tsc --noEmit`
clean; `npm run test` on the new test file passes.

---

### Out of scope

- Recurring subtasks (parent_task_id on occurrences).
- "Edit all past" occurrences.
- UI to view the full recurrence chain.
- Recurrence on the `/today` scheduler (v1 treats occurrences as regular tasks).
- Calendar sync.
- Any item on the README "do not build yet" list.

---

### Done criteria

1. `PATCH /api/tasks/{id}` with `repeat_interval` set persists the field.
2. Marking a recurring task done auto-creates the next occurrence with the
   correct due date.
3. Skip suppresses next-occurrence creation.
4. `edit_scope="future"` patches forward occurrences in a single request.
5. Setting `repeat_interval` without a `due_date` returns 422.
6. Month-boundary edge case (Jan 31 + 1 month) does not crash.
7. Repeat field in `TaskDetailPage` is disabled without a due date.
8. EditScopeModal appears when editing a recurring task.
9. Repeat badge visible on `TaskCard`.
10. All backend recurrence tests pass (`pytest tests/test_recurrence.py`).
11. Frontend recurrence tests pass.
12. `mypy --strict`, `ruff check`, `tsc --noEmit` clean.
13. No new dependency added without asking (month math: check for
    `python-dateutil` in `pyproject.toml` first).

---

### Verification targets

Backend:
```bash
cd backend && pytest tests/test_recurrence.py
cd backend && mypy --strict app/schemas/tasks.py app/services/tasks.py
```

Frontend:
```bash
cd frontend && npm run test -- Recurrence
cd frontend && npx tsc --noEmit
```

---

- Completed work lives in `DONE.md`.
- Incomplete items / follow-ups live in `TODO.md`.
- The sprint roadmap is in `README.md`.
