# Project phases — CURRENT FOCUS

> Promoted from the `TODO.md` backlog ("Features → Project phases") now that the
> Planning view (Gantt/calendar) epic is complete (Sprints 17–24). First-class
> phase/grouping support for tasks, surfaced in the planning views with
> collapse/expand and phase-level summary bars. Like the planning epic, this is
> split into ordered, individually-shippable **vertical** slices — each one ships
> UI → API → DB → UI with at least one happy-path test. **Slice 1 is the next
> sprint (Sprint 25).**

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## What a "phase" is

A **phase** is a named, ordered grouping of tasks **within one project** — e.g.
"Discovery", "Build", "Launch". It is a planning lens, not a scheduling primitive:
the app still owns all dates and the dependency cascade (CLAUDE.md prime
directive #1). A phase has no dates of its own — its bar on the Gantt is a
**derived summary** spanning its member tasks.

## Decisions (read before building)

1. **Phases are a first-class `phases` table, NOT parent tasks.** The `TODO.md`
   entry says keep phases separate from task nesting "unless the service model
   says phases should literally be parent tasks." They don't: a phase groups tasks
   that already nest (a task in a phase can still have subtasks), phases are flat
   (no phase-in-phase), and a phase carries ordering + a project scope a task
   parent doesn't. So: a new `phases` table + a nullable `tasks.phase_id` FK.
   Reusing `parent_task_id` would conflate two orthogonal trees.

2. **Phases are project-scoped and ordered.** `phases.project_id` (FK) +
   `phases.position` (int) for explicit ordering. A task's `phase_id`, when set,
   **must point to a phase in the task's own project** — guarded in Python (422 on
   mismatch), the same posture as the parent-cycle guard.

3. **Phase summary geometry is presentation, derived client-side.** Bar geometry
   already lives in the pure, unit-tested `ganttModel.ts` (the read-only display
   rule: *scheduling* is Python, *bar drawing* is frontend). A phase summary bar
   spans **earliest member-bar start → latest member-bar end**, computed by a new
   pure `phaseSummary()` there. *(Deviation from the TODO's "earliest start through
   latest due date" wording: a summary should visually encompass the bars it
   summarizes; stopping at the latest due date would leave longer-estimate bars
   poking past the summary. Flagging this — revert to due-date if preferred.)*
   Phase **identity, order, and membership** come from the backend; only the
   span math is frontend.

4. **Deleting a phase rehomes its tasks to "ungrouped" (`phase_id = NULL`).**
   Soft-delete the phase row, NULL out member `phase_id`s in the same transaction
   — mirrors the project-delete → rehome-to-General pattern. No task is lost or
   hidden; it just falls into the "No phase" group.

5. **Subtasks inherit their parent's `phase_id` at create-time only.** Same rule
   as project/priority/due-date inheritance in `create_task` — a sensible default
   the caller can override; re-phasing a parent later never clobbers children.

6. **AI-free.** No model call, no eval case, no prompt. This is pure
   app-owns-the-logic CRUD + presentation.

---

## Slice 1 — Phase model + management *(Sprint 25 — the next sprint)*

Stand up phases end-to-end **without** touching the Gantt yet: create/order/rename/
delete phases on a project, assign tasks to them, see the grouping in the task
list. This is the foundation Slices 2–3 render.

**Backend**
- [ ] `phases` table — `id`, `project_id` (FK → projects), `name`, `position` (int),
      `TimestampMixin` + `SoftDeleteMixin` (`deleted_at`, soft-delete like every
      user-facing table). `tasks.phase_id` — nullable FK → phases, `default=None`.
- [ ] **Alembic migration** (`alembic revision --autogenerate`, review before apply)
      — creates `phases` + adds `tasks.phase_id`. One migration, schema-only.
- [ ] `services/phases.py` (one responsibility — phase things only): `list_phases`
      (project-scoped, `active()`, ordered by `position`), `create_phase` (appends
      at next position), `rename_phase`, `reorder_phases` (set positions from an
      ordered id list), `delete_phase` (soft-delete + NULL member `phase_id`s in one
      transaction). Activity-log the lifecycle via `services/activity.py` like
      projects/tasks do.
- [ ] Guard in `services/tasks.update_task`: setting `phase_id` to a phase outside
      the task's project → raise → `422` (new `PhaseProjectMismatchError`, mapped in
      the route). `phase_id` added to the `update_task` field whitelist.
- [ ] `create_task`: seed `phase_id` from the parent when a subtask omits it
      (create-time only), alongside the existing project/priority/due inheritance.
- [ ] Schemas: `PhaseCreate` / `PhaseUpdate` / `PhaseRead` (+ `ReorderRequest`);
      add `phase_id: int | None` to `TaskRead`, `TaskCreate`, `TaskUpdate`.
- [ ] Routes (`api/routes_phases.py`): `GET /api/projects/{id}/phases`,
      `POST /api/projects/{id}/phases`, `PATCH /api/phases/{id}` (rename),
      `PATCH /api/projects/{id}/phases/reorder`, `DELETE /api/phases/{id}`
      (soft-delete). Register the router in `main.py`.

**Frontend**
- [ ] `api/phases.ts` + a `Phase` type; a `usePhases(projectId)` hook.
- [ ] Phase management on `ProjectDetailPage` — add / rename / reorder / delete,
      reusing the existing inline-edit + confirm-before-delete patterns (mirrors the
      alias-management UI from Sprint 9e).
- [ ] A **Phase** dropdown (scoped to the task's project, "No phase" = null) in
      `TaskFormModal` and on `TaskDetailPage`'s inline editor; a phase badge on
      `TaskCard`.

**Done when**
- [ ] pytest: phase CRUD, ordered list, project-match guard (422), member rehome on
      delete, subtask phase inheritance. Frontend: phase dropdown + management tests.
- [ ] Migration committed; README schema section + sprint status updated.
- [ ] No model/eval/prompt/dependency change.

---

## Slice 2 — Phases in the per-project Gantt *(next)*

The planning payoff: group the per-project timeline by phase, draw a derived
**summary bar** per phase, and collapse/expand each phase.

- [ ] `ProjectGantt` payload gains `phases: list[GanttPhase]` (id, name, position);
      `TaskRead.phase_id` already lands in Slice 1, so the renderer can group.
- [ ] `ganttModel.ts`: group bars by phase (ordered by `position`; phase-less bars
      in a synthetic "No phase" group, last) + a pure, unit-tested `phaseSummary()`
      → `{ start, end }` (earliest member start → latest member end).
- [ ] `GanttChart`: extend the existing `Row` union (`group | bar`) with a `phase`
      header row carrying its summary bar; per-phase collapse/expand (collapsed →
      only the summary bar; expanded → summary + member bars). Collapse state is
      local UI state. The summary bar is read-only (derived — no drag/resize handle,
      a tooltip explains why, mirroring the parent-bar resize opt-out).
- [ ] Drag/resize/Fix/what-if all keep operating on member bars unchanged.
- [ ] Tests: `ganttModel` grouping + `phaseSummary` unit tests; `TimelinePage`
      collapse/expand + summary-render tests. Reuses the Slice-1 endpoint (only the
      payload grows) — no schema change.

---

## Slice 3 — Phases on the global `/planning` surface *(later; may be deferred)*

- [ ] `GlobalGantt` gains per-project `phases`; `GanttChart` grouping goes two-level
      (project section → phase sub-rows → bars), with collapse/expand at both levels.
- [ ] Tests for the nested grouping. Scope-check before starting — if Slice 2 lands
      the value, this can stay backlog rather than sprawl.

---

## Out of scope for this epic

- Phase-level dependencies or scheduling (phases have no stored dates; the task
  dependency graph stays the unit of scheduling).
- Phase templates / cross-project phase libraries.
- Any AI involvement (no "suggest phases" workflow — that would be a separate,
  later, training-data-bearing slice if ever).
