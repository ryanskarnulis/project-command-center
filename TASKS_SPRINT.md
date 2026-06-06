# Sprint 8 — Task & Inbox UX Overhaul

> Goal: turn the task/project surface into clickable detail views built on one shared task
> card, with smarter ordering, a filter, customizable estimates, and an inbox that is
> review-only with per-candidate approval. Ship as **8 separate small PRs** (CLAUDE.md:
> small reviewable diffs, one slice at a time). Slices **5 → 6 → 8** are ordered — the shared
> card and the detail view are prerequisites for the inbox rework. The rest are independent.

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

## Ground rules (read first)

- **Backend touches only Slices 3 and 8. No schema change / no Alembic migration in any slice.**
  If you reach for a migration, stop — the columns you need (`tasks.status`, `tasks.project_id`,
  `tasks.parent_task_id`, `inbox_items.reviewed_at`) already exist.
- Frontend: React + Vite + TS strict, no `any` without a `// TODO` + reason. API calls go through
  `src/api/`; components consume hooks. Plain CSS in `src/index.css` (no CSS modules). Feature
  folders, not type folders.
- Backend: SQLAlchemy 2.0 typed syntax, Pydantic v2, structlog with request IDs, type hints on
  every signature, soft-delete filter via the `active()` helper.
- Per slice: `cd frontend && npm run test && npm run build` green; backend slices add `pytest`.
- One-line commit per slice at the chunk stop, per convention: `<letter>: Sprint 8 - <slice> (...)`.

## Request → slice map (the 13 asks)

| Request | Slice |
| --- | --- |
| Order same-due tasks by priority | 1 |
| Keep nested tasks together | 1 |
| Drop the Tasks tab (Open Tasks card already links there) | 2 |
| Project links show the real name, not `Project #` | 2 |
| Fresh subtasks inherit the parent task's project | 3 |
| Fully-customizable time estimate (no dropdown) | 4 |
| Better-looking clickable task card (shared everywhere) | 5 |
| Special task detail view with subtasks; subtask + project-task click-through; projects same style | 6 |
| Add-task button opens a modal like the edit modal | 6 |
| Filter for the tasks view | 7 |
| Inbox shows only awaiting-approval items, each as the task card | 8 |
| Approve candidates one at a time | 8 |

---

## Slice 1 — Ordering + nesting integrity  *(FE-only)*

**Files:** `frontend/src/utils/dates.ts`, `frontend/src/utils/dates.test.ts`,
`frontend/src/features/tasks/TasksPage.tsx` (swap the comparator).

- [x] Add a priority rank map: `{ urgent: 0, high: 1, medium: 2, low: 3 }` (lower sorts first).
- [x] Add `compareTasks(a, b)`: due date ascending (nulls last, as `compareByDue` already does) →
      **then priority rank** → then `id`. Kept `compareByDue` unchanged for the dashboard's
      due-only sort. The new comparator uses a `HasDueAndPriority` interface.
- [x] `TasksPage` already renders a tree (`roots` + `childrenOf`) and sorts each sibling level with
      `compareByDue`; switched both `.sort(compareByDue)` calls to `compareTasks`.
- [x] Unit tests: equal due dates resolve by priority; mixed due dates still sort by due first;
      nulls last; deterministic `id` tie-break.

## Slice 2 — Nav + project-name links  *(FE-only)*

**Files:** `frontend/src/components/AppShell.tsx`, `frontend/src/components/AppShell.test.tsx`,
`frontend/src/features/tasks/TasksPage.tsx`.

- [x] Remove the `{ to: '/tasks', label: 'Tasks', icon: ListTodo }` entry from `primaryNav` in
      `AppShell`. Left the `/tasks` route in `AppRoutes`. Dropped unused `ListTodo` import.
- [x] `AppShell.test.tsx`: updated to assert Tasks link is absent and the remaining 3 links are present.
- [x] In `TasksPage.renderTask`, the global view now resolves project name via
      `projects.find(p => p.id === t.project_id)?.name ?? 'Project'`. Moved into `TaskCard` (Slice 5).

## Slice 3 — Subtask project inheritance  *(BE + pytest)*

**Files:** `backend/app/services/tasks.py`, `backend/tests/` (task service test).

- [x] In `create_task`, before the `_default_project_id_for_status` call: when
      `parent_task_id is not None` and `project_id is None`, looks up the parent and inherits
      its `project_id`. Default-for-status logic runs after, so `None` parent + accepted still
      falls back to General.
- [x] Documented inline: inheritance applies to **create** only; `update_task` unchanged.
- [x] Happy-path pytest: `test_subtask_inherits_parent_project_when_none_given` and
      `test_subtask_keeps_explicit_project_when_given` — both pass.
- [x] No migration. README note not required.

## Slice 4 — Customizable estimate input  *(FE-only)*

**Files:** `frontend/src/utils/duration.ts` (add a minutes→{value,unit} split + parse helper),
the task edit form (currently `TaskEditModal.tsx`; may live in the shared form after Slice 6).

- [x] Replaced the `DURATION_OPTIONS` `<select>` with a free **number input + unit `<select>`**
      (minutes / hours / days / weeks). Empty value → `null`. Client rejects `min={1}`.
- [x] `formatDuration` updated to use `splitDuration` internally; `splitDuration` and `toMinutes`
      added. `DURATION_OPTIONS` removed (was unused after the change).
- [x] Vitest for `toMinutes` / `splitDuration` round-trips — `duration.test.ts` (20 cases).

## Slice 5 — Shared `TaskCard` component  *(FE-only, foundational — do before 6 & 8)*

**Files (new):** `frontend/src/features/tasks/TaskCard.tsx`, `TaskCard.test.tsx`.
**Files (modify):** `frontend/src/features/tasks/TasksPage.tsx`, `frontend/src/index.css`.

- [x] `TaskCard` props: `task: Task`, `projects?: Project[]`, `actions?: ReactNode`. Uses the link
      form (`<Link to="/tasks/:id">`). Renders: title, status-pill, priority-pill, due badge,
      estimate badge, Blocked badge, project name (when `projects` provided).
- [x] Row actions passed via `actions` prop into `.task-card-actions` with `e.preventDefault()`
      so clicking an action doesn't trigger card navigation.
- [x] Swapped `TasksPage.renderTask`'s inline `<li>` markup to render `TaskCard`. Tree/indent
      and per-row subtask composer stay in `TasksPage` (kept inline — simpler than opening a modal).
- [x] CSS: `.task-card`, `.task-card-body`, `.task-card-badges`, `.task-card-actions`,
      `.priority-pill.*`, `.status-pill.*` added. Dark-mode overrides in media block.
- [x] `TaskCard.test.tsx`: 9 tests — title, link target, badges, blocked/done, estimate, project name.

## Slice 6 — Task detail view + add/edit modal unification  *(FE + small BE)*

**Files (BE):** `backend/app/api/routes_tasks.py` (+ test).
**Files (FE, new):** `frontend/src/features/tasks/TaskDetailPage.tsx`,
`frontend/src/features/tasks/TaskFormModal.tsx` (or generalize `TaskEditModal`), `*.test.tsx`.
**Files (FE, modify):** `frontend/src/routes/AppRoutes.tsx`, `TasksPage.tsx`,
`frontend/src/api/tasks.ts` (+ `getSubtasks`), `frontend/src/features/tasks/useTasks.ts`.

- [x] **BE:** `GET /api/tasks/{id}/subtasks` → `TaskRead[]` added to `routes_tasks.py`.
      `is_blocked` resolved with `_reads_with_blocked`. 404 if parent task missing.
      pytest: `test_list_subtasks_route_returns_direct_children` + `test_list_subtasks_route_404_for_missing_task`.
- [x] **FE route** `/tasks/:taskId` → `TaskDetailPage`: `TaskDetailPage.tsx` created; fetches
      task + subtasks + projects; renders subtasks as `TaskCard`s + `TaskDependencies`; back link;
      loading/error/404 states. Route added to `AppRoutes.tsx`. `getSubtasks` added to `src/api/tasks.ts`.
- [x] **Click-through:** `TaskCard` already links to `/tasks/:id` (Slice 5) — free.
- [x] **Add-task modal:** `TaskFormModal.tsx` created — create + edit modes. `TasksPage` now
      uses `TaskFormModal` for both add and edit. Inline add-task `<form>` replaced with
      **Add task** button. Per-row subtask composer stays inline (chosen over modal — preserves
      quick inline flow without an extra overlay).
- [x] **Projects "same style":** `TasksPage` (scoped) already uses `TaskCard` from Slice 5.
      No separate ProjectDetailPage — `/projects/:id/tasks` reuses `TasksPage` unchanged.
- [x] All existing tests still pass (72 FE, 143 BE); no test relied on the old inline form shape.

## Slice 7 — Task filter  *(FE-only)*

**Files:** `frontend/src/features/tasks/TasksPage.tsx` (+ a small `TaskFilters.tsx` if it grows),
`TasksPage.test.tsx`.

- [x] Filter controls above the list: **status**, **priority**, **project** (global only),
      toggles for **overdue / due-soon / blocked**. State held in `TasksPage`; client-side filtering.
- [x] Filter flat list first, then rebuild `roots`/`childrenOf`. A matching child whose parent
      doesn't match is promoted to root (same orphaned-parent fallback the tree-builder already uses).
- [x] "Clear filters" button (shown only when active); empty-filter state shows distinct message
      "No tasks match the current filters." vs. "No tasks yet."
- [x] Tests: filter by status, filter by priority, Clear filters restores all tasks, empty-result message.

## Slice 8 — Inbox = review-only, per-candidate approval  *(BE + FE)*

**Files (BE):** `backend/app/services/review.py`, `backend/app/api/routes_inbox.py`,
`backend/app/schemas/inbox.py` (per-candidate decision shape), `backend/tests/`.
**Files (FE):** `frontend/src/features/inbox/InboxPage.tsx`, `useInbox.ts`, `api/inbox.ts`,
remove/retire `InboxCapturePanel` usage on `/inbox` and `ReviewQueue` (replaced by cards);
`InboxPage.test.tsx`.

### Backend — per-candidate decision + deferred finalization
- [ ] Split review into a **per-candidate** operation:
  - **Approve**: flip the candidate `status` → `accepted` and resolve its `project_id` (reuse the
    `_resolve_project_id` guard). Field edits arrive first via the existing
    `PATCH /api/tasks/{id}` from the detail/edit view, so approve mainly flips status + files it.
  - **Dismiss**: flip `status` → `rejected`.
- [ ] **Finalization (preserves prime directive #4):** after each decision, if **no
      `candidate`-status tasks remain** for the inbox item, set `reviewed_at` and write the single
      `ai_training_examples` row (corrected output = the note's `accepted` tasks, current values) +
      the `project_matching` row (when `match_output_json` is set and ≥1 task accepted) — the same
      payloads `review_inbox` builds today, just emitted at the end of the per-candidate flow
      instead of from a batch call. Keep it atomic (flush within a try / rollback on error).
- [ ] Keep `AlreadyReviewedError` semantics: a finalized note (reviewed_at set) rejects further
      decisions. Decisions referencing a non-candidate task of the item still 4xx.
- [ ] Route(s): add a per-candidate endpoint (e.g. `POST /api/inbox/{id}/candidates/{task_id}`
      with `{action: "approve"|"dismiss"}`), or `POST /api/tasks/{id}/approve|dismiss` scoped to
      candidate-status tasks — pick one and note it. The existing batch
      `POST /api/inbox/{id}/review` can stay (dashboard capture panel still uses it) or be
      expressed in terms of the new per-candidate path; don't break the dashboard flow.
- [ ] pytest: approve one of two candidates → note **not** finalized, no training row yet; decide
      the second → note finalized, exactly **one** training row with both outcomes reflected;
      re-deciding a finalized note → `AlreadyReviewedError`.

### Frontend — review-only inbox with task cards
- [ ] `/inbox` (`InboxPage`) drops the capture textarea entirely (capture lives on the dashboard).
      It lists notes awaiting review; each note's candidates render as **`TaskCard`s**.
- [ ] Opening a candidate card → the task detail/edit view (Slice 6) where you edit fields, then
      **Submit** (approve → per-candidate endpoint) or **Dismiss** (reject). After a decision,
      refresh the pending list; when a note's last candidate is decided it drops out.
- [ ] `useInbox`/`api/inbox`: add the per-candidate approve/dismiss calls; keep `loadPending`.
      Retire `ReviewQueue` from `/inbox` (and `InboxCapturePanel` from `/inbox` — it stays on the
      dashboard). Keep training-clean error messaging (the 409 already-reviewed copy).
- [ ] Vitest: a pending note renders candidate cards; dismiss removes one; approving the last
      finalizes (mock the API).

---

## Verification (per slice, definition of done)

1. `cd frontend && npm run test` — new + existing Vitest suites green.
2. `cd frontend && npm run build` — strict TS build passes (no stray `any`).
3. Backend slices (3, 8): `cd backend && pytest` green; structured logs carry the request ID.
4. Manual (backend + Ollama per README dev commands):
   - **1:** two tasks same due date → higher priority first; subtasks stay under their parent.
   - **2:** no Tasks item in the sidebar; global task view shows project **names**; dashboard Open
     Tasks card still opens `/tasks`.
   - **3:** add a subtask to a task in project P (no project chosen) → it lands in P.
   - **4:** set an estimate as "2 hours" → badge shows the formatted label; `0` rejected.
   - **5/6:** click a task card → detail view with its subtasks; click a subtask → its detail view;
     **Add task** opens a modal; a project's tasks render the same cards.
   - **7:** filter by status/priority/project → only matching cards; nesting preserved.
   - **8:** `/inbox` has no capture box, only awaiting-review notes as cards; open a candidate →
     edit → Submit files it; Dismiss rejects it; deciding the last candidate writes exactly one
     `ai_training_examples` row (check the DB / `/training`).

## Bookkeeping

- Check off each slice in `TASKS.md` (Sprint 8 → "Task & Inbox UX overhaul") as it lands.
- README update **only** for Slice 6 (new `GET /api/tasks/{id}/subtasks` route) and Slice 8 (inbox
  is now review-only + per-candidate approval changes the documented flow) — note schema is
  unchanged. Other slices need no README change; say so in the commit per the CLAUDE.md checklist.
- One-line commit per slice at the chunk stop.
