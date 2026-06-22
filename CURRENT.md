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
`useBarResize` gesture hook + `useProjectGantt.resize`.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Next up — Slice 4: Dependency lines + conflict warnings + autofix

SVG overlay arrows between dependent bars; flag violations (dependent starts
before its blocker ends); offer an autofix action.

> Known a11y gap (slices 2 & 3): drag is the **only** UI that writes
> `scheduled_start`, and the right-edge handle the **only** one that writes
> `estimated_minutes` from the timeline — no keyboard/non-drag path for either.
> Flagged for a later a11y pass.

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
- [ ] **4. Dependency lines + conflict warnings + autofix** — SVG overlay arrows between
      dependent bars; flag violations (dependent starts before its blocker ends); offer an
      autofix action.
- [ ] **5. Python dependency auto-shift (service layer)** — when a task date changes
      inside a dependency tree, downstream dependents shift per the graph. Pure Python in
      `services/planning.py` with tests; first-class, not frontend date math.
- [ ] **6. What-if mode** — staged, unsaved schedule experiments computed through the
      same Python shift rules, with commit/discard.
- [ ] **7. Zoom levels (day/week/month)** — plus blocked-task visualization polish.
- [ ] **8. Global planning surface + calendar view** — cross-project timeline and the
      calendar variant (reuse the existing `/calendar` work where possible).
- [ ] **9. Drag from the unscheduled bucket onto the chart** — schedule a task that has
      no due date or estimate by dragging it in.
