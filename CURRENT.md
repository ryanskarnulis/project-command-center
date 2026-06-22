# Current focus — Planning view (Gantt/calendar)

The old single "Calendar/Gantt planning view" bullet was an epic (~8 features in
one) and the first build attempt sprawled, so it's split into the ordered,
individually-shippable slices below. Each slice is a vertical slice with at least
one happy-path test.

**Decision:** render with a **custom CSS/SVG Gantt**, not a third-party library.
The frappe-gantt attempt was abandoned — vanilla-JS imperative lib, wrong shape
for React, styling un-tameable.

**Shipped so far:** Slice 1 (static read-only project Gantt) landed in Sprint 17
(see `DONE.md`). The `GanttChart` renderer, `ganttModel`, the
`/projects/:id/timeline` route, and the `scheduled_start` column + PATCH plumbing
are all in place. **Slice 2 (drag-to-reschedule)** is now shipped: bars drag
horizontally to set `scheduled_start` via the existing PATCH, with an optimistic
move, revert-on-error, and a toast (`useDragReschedule` gesture hook +
`useProjectGantt.reschedule`). **Slice 3 (bar-resize to edit estimate)** is now
shipped: a right-edge handle on each bar drags to set `estimated_minutes` (one
day-column = 480 min, min 1 day) via the existing PATCH, with optimistic resize,
revert-on-error, and a toast. Parent bars (whose estimate is a server rollup of
their subtasks) expose **no** resize handle — a tooltip says the estimate rolls
up from subtasks — since a parent estimate is not directly settable. New
`useBarResize` gesture hook + `useProjectGantt.resize`. **Slice 4 (dependency
lines + conflict warnings + autofix)** is now shipped: an SVG overlay draws
finish-to-start arrows between dependent bars (measured from the rendered rects so
they track flexing columns + horizontal scroll); a dependent scheduled on or
before its blocker finishes is flagged (red arrow + a warning ring on the bar) and
listed in a Conflicts panel with a one-click **Fix** that nudges its
`scheduled_start` to `blocker.end + 1` via the existing `reschedule` PATCH (one
task, one PATCH — no cascade, that is Slice 5). New `dependencyConflicts.ts` (pure,
unit-tested) + `DependencyArrows.tsx` overlay. **Slice 5 (Python dependency
auto-shift)** is now shipped: changing a task's `scheduled_start` or
`estimated_minutes` via the task PATCH cascades the move through the dependency
graph server-side, pushing every downstream dependent forward just enough that none
starts on or before a blocker finishes (the generalization of Slice 4's single-task
Fix). Pure, side-effect-free `compute_shifts` (topological walk, finish-to-start
`blocker.end + 1`, unscheduled tasks neither move nor anchor) in
`services/planning.py`, applied in one transaction by `cascade_downstream` and
fired from `routes_tasks.update_task` only when a placement field changed. The
timeline's existing post-PATCH refetch surfaces the shifted bars — no new frontend
date math (CLAUDE.md prime directive #1). New `test_planning_shift.py` (pure unit)
+ route-level cascade tests in `test_planning.py`. **Slice 6 (What-if mode)** is now
shipped: a "What-if mode" toggle on the timeline turns drag/resize/Fix into *staged*
changes rather than persisting them. Each stage POSTs the override set to
`POST /api/projects/{id}/gantt/what-if`, which layers the overrides onto the real
placements and runs the *same* pure `compute_shifts` the committed cascade uses —
read-only, nothing persisted (new side-effect-free `preview_shifts` in
`services/planning.py`, the read-side twin of `cascade_downstream`). The chart
overlays the returned starts so the hypothetical schedule renders in place; **Apply**
commits each staged change via the ordinary task PATCH (which cascades for real),
**Discard** drops it. No new frontend date math — every previewed start comes from
Python. New `useWhatIf` hook + `previewWhatIf` API; route-level preview tests in
`test_planning.py` + TimelinePage what-if tests.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Next up — Slice 7: Zoom levels (day/week/month)

Day/week/month zoom on the timeline axis, plus blocked-task visualization polish.
The renderer is a single CSS grid keyed off `--gantt-cols` and the per-bar
`gridColumn` math in `GanttChart`, so a zoom level is a different day→column
bucketing of the same `buildGanttModel` bars — still no third-party library, still
no scheduling math in the frontend.

> Known a11y gap (slices 2 & 3): drag is the **only** UI that writes
> `scheduled_start`, and the right-edge handle the **only** one that writes
> `estimated_minutes` from the timeline — no keyboard/non-drag path for either.
> The Slice 4 **Fix** button is a non-drag path that writes `scheduled_start`, but
> only for conflict resolution — the general a11y gap remains. Flagged for a later
> a11y pass.

---

## Remaining slices

- [x] **1. Static read-only project Gantt (custom renderer)** — shipped, Sprint 17.
- [x] **2. Drag-to-reschedule** — shipped. Horizontal bar drag sets `scheduled_start`
      via the existing task PATCH; optimistic move + revert-on-error + toast. New
      `useDragReschedule` gesture hook (measures the flexing day-column width from the
      DOM) + `useProjectGantt.reschedule`; rebuilt against the real `useToast` API.
- [x] **3. Bar-resize to edit estimate** — shipped. A right-edge handle drags to set
      `estimated_minutes` (one day-column = 480 min, min 1 day) via the existing task
      PATCH; optimistic resize + revert-on-error + toast. Parent bars expose no handle
      (their estimate is a server rollup of subtasks, not directly settable) — a tooltip
      explains why. New `useBarResize` gesture hook
      (mirrors `useDragReschedule`) + `useProjectGantt.resize`.
- [x] **4. Dependency lines + conflict warnings + autofix** — shipped. SVG overlay draws
      finish-to-start arrows between dependent bars (geometry measured from the rendered
      rects, so they track the flexing day columns + horizontal scroll). A dependent
      scheduled on or before its blocker finishes is flagged (red arrow + bar warning ring)
      and listed in a Conflicts panel with a one-click **Fix** that sets its
      `scheduled_start` to `blocker.end + 1` via the existing `reschedule` PATCH (one task,
      one PATCH — no cascade). New `dependencyConflicts.ts` (pure, unit-tested) +
      `DependencyArrows.tsx`.
- [x] **5. Python dependency auto-shift (service layer)** — shipped. Changing a task's
      `scheduled_start`/`estimated_minutes` via the task PATCH cascades the shift through
      the dependency graph server-side: a pure, unit-tested `compute_shifts` (topological,
      finish-to-start `blocker.end + 1`, unscheduled tasks excluded) applied in one
      transaction by `cascade_downstream`, fired from `routes_tasks.update_task`. The
      timeline's existing post-PATCH refetch surfaces the moved bars (no new frontend date
      math). New `test_planning_shift.py` + route-level cascade tests.
- [x] **6. What-if mode** — shipped. A "What-if mode" toggle turns drag/resize/Fix into
      *staged* changes: each stage POSTs the override set to
      `POST /api/projects/{id}/gantt/what-if`, which runs the *same* pure `compute_shifts`
      over the real placements with the overrides layered on — read-only, nothing
      persisted (new `preview_shifts`). The chart overlays the returned starts; **Apply**
      commits via the ordinary task PATCH (which cascades for real), **Discard** drops it.
      No new frontend date math. New `useWhatIf` hook + `previewWhatIf` API.
- [ ] **7. Zoom levels (day/week/month)** — plus blocked-task visualization polish.
- [ ] **8. Global planning surface + calendar view** — cross-project timeline and the
      calendar variant (reuse the existing `/calendar` work where possible).
- [ ] **9. Drag from the unscheduled bucket onto the chart** — schedule a task that has
      no due date or estimate by dragging it in.
