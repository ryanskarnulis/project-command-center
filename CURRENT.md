# Current Sprint

## Next Sprint — Today / Daily Schedule

Build a daily-use `/today` surface that turns the existing task data into a
practical plan for the day. This is the strongest next sprint from `TODO.md`
because it uses features already shipped — due dates, priorities, workflow
status, estimates, and dependency-derived blocking — without crossing into the
README's "do not build yet" list.

**Decision:** Defer README Sprint 10 (`ai_training_examples` export →
Unsloth fine-tune → llama.cpp swap) until the training corpus reaches the
200-example gate. This sprint promotes the TODO "Today / Daily Schedule" backlog
item instead.

**Scope guard:** initial scheduler is pure Python, no model call. No calendar
sync, no external calendar integration, no custom model work, no autonomous
planning, no schema changes, and no Alembic migration. The app owns the logic:
the schedule is deterministic, explainable, and guarded by existing task state.

**UI entry point:** do not add Today to the sidebar. The schedule opens from the
existing dashboard "Today's Tasks / Due Soon" focus tile. Clicking the tile's
"View due work" action (and optionally the tile body if that fits the existing
card pattern) navigates to `/today`.

### Product goal

The user opens `/today` and sees:

- A prioritized timeline of work blocks for the selected day.
- Overdue and due-today work pulled forward automatically.
- High-priority and in-progress tasks used to fill available capacity when
  nothing is formally due.
- Blocked tasks called out separately instead of being scheduled.
- Unscheduled overflow shown honestly when the day is full.

### Scheduling rules for v1

- Source tasks: active, accepted tasks with `workflow_status != done`.
- Exclude from timeline: tasks where derived `is_blocked` is true.
- Still surface blocked tasks in a separate "Blocked" section.
- Default unsized tasks to 30 planning minutes, but label that estimate as
  assumed so the UI does not pretend it came from the task.
- Score tasks deterministically:
  - `in_progress` before `open`
  - overdue before due today
  - due soon before no due date
  - priority order: urgent, high, medium, low
  - shorter tasks win only as a tie-breaker when the day has limited room
- Build sequential blocks from the requested start time until available minutes
  are exhausted.
- Return overflow tasks in ranked order, not silently hidden.

### Files in play

Backend:

- `backend/app/schemas/today.py` — response/request-facing plan schemas.
- `backend/app/services/today.py` — deterministic scheduling logic.
- `backend/app/api/routes_today.py` — `GET /api/today`.
- `backend/app/main.py` — register the new router.
- `backend/tests/test_routes_today.py` — route + scheduling behavior tests.

Frontend:

- `frontend/src/types/today.ts` — Today plan types.
- `frontend/src/api/today.ts` — API call with query params.
- `frontend/src/features/today/useTodayPlan.ts` — loading/error/refetch hook.
- `frontend/src/features/today/TodayPage.tsx` — timeline and sections.
- `frontend/src/features/today/TodayPage.test.tsx` — page smoke/behavior tests.
- `frontend/src/routes/AppRoutes.tsx` — add `/today`.
- `frontend/src/features/dashboard/DashboardPage.tsx` — wire the "Today's Tasks
  / Due Soon" tile to `/today`.
- `frontend/src/index.css` — today-* styles using existing design tokens.

### Chunk plan

**Chunk A — Backend plan contract + scheduler** ✅ done

- Add `today` schemas for plan metadata, scheduled blocks, overflow tasks, and
  blocked tasks.
- Implement `services/today.py` with a small pure function for ranking and a
  service function that loads open accepted tasks, resolves blocked IDs in bulk,
  and returns a plan.
- Use frontend-passed `date` for the target day; default on the backend only
  when omitted.
- Add tests for overdue-first ordering, blocked exclusion, assumed estimates,
  and overflow behavior.

Landed:
- `backend/app/schemas/today.py` — `DueSignal` enum + `ScheduledBlock`,
  `OverflowTask`, `BlockedTask`, `TodayPlan`.
- `backend/app/services/today.py` — pure `_rank_key`/`_due_signal`/`_pack`
  helpers + `get_today_plan()`; bulk blocked-ID resolution (no N+1); blocked
  tasks surfaced separately; stop-at-first-nonfit packing.
- `backend/tests/test_today.py` — 8 tests (due-urgency order, in-progress-first,
  priority order, blocked exclusion + surfacing, done-dependency unblock, assumed
  estimate, ranked overflow, sequential block times).
- Verification: `pytest tests/test_today.py` → 8 passed; `mypy --strict` clean;
  `ruff check` clean. No route, no `main.py` change, no migration, no model call.

**Chunk B — Backend route** ✅ done

- Add `GET /api/today`.
- Query params:
  - `date=YYYY-MM-DD` optional target date.
  - `start_time=HH:MM` optional, default `09:00`.
  - `available_minutes` optional, default `360`, with a sane bounded range.
- Register the router in `main.py`.
- Log `today_plan_generated` with task counts and selected date.

Landed:
- `backend/app/api/routes_today.py` — `GET /api/today` with `tags=["today"]`.
  Validation pushed to the boundary so the scheduler never sees junk: `date`
  typed as `date | None` (FastAPI rejects bad ISO → 422), defaults to
  `date.today()` when omitted; `start_time` constrained by regex
  `^([01]\d|2[0-3]):[0-5]\d$`; `available_minutes` bounded `ge=15, le=1440`.
  Query defaults reuse the service's `DEFAULT_*` constants so route and scheduler
  can't drift. Logs `today_plan_generated` with date, start_time,
  available/used minutes, and scheduled/overflow/blocked counts.
- `backend/app/main.py` — import + register `routes_today.router`. Endpoint is
  `GET /api/today`.
- `backend/tests/test_routes_today.py` — 6 tests (happy path, default-to-today,
  start/capacity pass-through with overflow, malformed start_time → 422,
  out-of-range capacity → 422, malformed date → 422).
- Verification: `pytest tests/test_routes_today.py` → 6 passed; `mypy --strict`
  clean; `ruff check` clean. No schema change, no migration, no model call.

**Chunk C — Frontend data layer + route** ✅ done

- Add Today types and API wrapper.
- Add `useTodayPlan` with date/start/capacity state and refetch behavior.
- Register `/today`.
- Leave the sidebar navigation unchanged.
- Update the dashboard "Today's Tasks / Due Soon" tile action from a due-work
  task filter link to `/today`.

Landed:
- `frontend/src/types/today.ts` — `DueSignal` + `ScheduledBlock`, `OverflowTask`,
  `BlockedTask`, `TodayPlan`, mirroring the Pydantic schemas and reusing
  `TaskPriority`/`TaskWorkflowStatus`.
- `frontend/src/api/today.ts` — `getTodayPlan({ date?, startTime?, availableMinutes? })`;
  omitted params fall through to backend defaults so state and request never drift.
- `frontend/src/features/today/useTodayPlan.ts` — date/start/capacity state +
  `refetch`; same `active`-flag cleanup pattern as the other refetch hooks.
- `frontend/src/routes/AppRoutes.tsx` — `/today` route; sidebar untouched.
- `frontend/src/features/dashboard/DashboardPage.tsx` — focus tile now links to
  `/today` (was the `/tasks?overdue=...` filter).

**Chunk D — Today page timeline** ✅ done

- Build the `/today` page with the existing page-header/card/filter visual
  language.
- Show date, start time, and capacity controls.
- Render scheduled blocks as a readable timeline with start/end time, estimate,
  priority, due signal, project/task link, and the deterministic reason.
- Reuse `TaskCard` where it helps, but keep timeline rows compact enough for
  daily scanning.

Landed:
- `frontend/src/features/today/TodayPage.tsx` — `section-heading` header, a
  controls row (date/start-time/capacity), a capacity summary line, and an
  ordered timeline of compact rows. Reuses the shared `priority-pill`,
  `status-pill workflow-*`, and `due-*` pill classes rather than `TaskCard`,
  which is too tall for daily scanning. Assumed estimates carry an "assumed" tag.
- Capacity is a preset `<select>` (2h/4h/6h/8h/10h) bounded inside the backend's
  15–1440 range; an off-preset value is injected so a deep-linked value still
  renders.

**Chunk E — Overflow, blocked, and empty states** ✅ done

- Add separate sections for:
  - Overflow tasks that did not fit.
  - Blocked tasks with a dependency warning.
  - Empty plan states when there are no open tasks or no schedulable tasks.
- Link each task to `/tasks/:id`.
- Keep blocked tasks out of the schedule unless dependencies are resolved.

Landed (in `TodayPage.tsx`):
- "Didn't fit" section — ranked overflow rows, each linking to `/tasks/:id`.
- "Blocked" section — dependency warning listing the unfinished blocking task
  ids, each linked.
- Empty state distinguishes "no open tasks" from "everything is blocked"
  (scheduled empty + blocked present + no overflow).

**Chunk F — Tests and documentation touch-up** ✅ done

- Add frontend tests for loading, scheduled timeline rendering, and blocked /
  overflow sections.
- Run targeted backend and frontend tests.
- Update `README.md` sprint plan and `TODO.md` only after implementation lands,
  moving this sprint from proposed/current to done/follow-up status.

Landed:
- `frontend/src/features/today/TodayPage.test.tsx` — 3 tests (scheduled timeline
  renders with task link + assumed tag + reason; overflow + blocked sections with
  dependency warning; empty state). `npm run test -- TodayPage` → 3 passed.
- `frontend/src/index.css` — `today-*` styles on existing tokens; no framework.
- Verification: `tsc --noEmit` clean; `pytest tests/test_routes_today.py` +
  `tests/test_today.py` still green. Lint: the one `set-state-in-effect` notice on
  `useTodayPlan` matches the existing refetch-hook convention (`useCompletedTasks`,
  `useSearch`), which already trips the same rule repo-wide.
- `README.md` sprint status and `TODO.md` updated to mark Today / Daily Schedule
  shipped.

### Out of scope

- AI reordering or "why this order" prose.
- Calendar sync or scheduling around meetings.
- Persisted workday preferences.
- Recurring tasks.
- Kanban.
- Command-bar slash actions.
- Training export / fine-tuning / llama.cpp swap.

### Done criteria

- `/today` works end-to-end: frontend → `GET /api/today` → deterministic Python
  scheduler → timeline UI.
- The dashboard "Today's Tasks / Due Soon" tile opens the schedule page.
- Overdue/due-today/high-priority/in-progress tasks sort predictably.
- Blocked tasks are never placed into active schedule blocks.
- Missing estimates are handled without crashing and are visibly marked as
  assumed.
- Overflow is visible when capacity is full.
- At least one backend happy-path test and one scheduling edge-case test pass.
- At least one frontend Today page smoke test passes.
- No model calls, no schema migration, no new dependency.

### Verification target

Backend:

```bash
cd backend && pytest tests/test_routes_today.py
```

Frontend:

```bash
cd frontend && npm run test -- TodayPage
```

Full regression pass is nice if the known flaky `TaskDetailPage.test.tsx` does
not interfere.

---

- Completed work lives in `DONE.md`.
- Incomplete items / follow-ups live in `TODO.md`.
- The sprint roadmap is in `README.md`; custom-model Sprint 10 remains gated on
  200+ active `ai_training_examples` rows.
