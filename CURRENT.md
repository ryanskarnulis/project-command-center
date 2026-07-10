# Current focus

**Epic: Dashboard redo — board-first UI** (checked out 2026-07-09).

The strip epic is done (see `DONE.md` / git history). Before Phase 2 (local
agent) starts, the UI gets reshaped around doing work instead of summarizing
it: the dashboard becomes a project-swimlane kanban, "Today" becomes "Focus",
project task views default to the board, and project aliases (an AI-extraction
leftover) are removed.

Decisions already made (don't relitigate):

- **Dashboard layout: project swimlanes**, not a flat global board. Rows =
  projects, columns = Open / In progress. The lane header carries the
  per-project overview (open count, status tone) that the old metric
  cards/workload bars provided. Done tasks are not a grid column; they live
  behind a per-lane toggle (fed from the completed archive, as `KanbanBoard`
  does today). Drag between columns works per-lane and routes through the same
  status-change paths as the existing board (recurrence-safe done/reopen,
  `is_blocked` move guard).
- **Signal strip stays**: one slim line above the board — overdue · blocking ·
  due today counts, each clickable to filter the board. This is the only old
  dashboard signal the board can't show at a glance; everything else
  (metric cards, workload bars, greeting hero copy) goes.
- **Today → Focus, full rename**: nav label, `/focus` route (keep a `/today`
  redirect), `features/today/` → `features/focus/`, and the backend today
  service/endpoint/schemas renamed too. No external consumers; vocabulary
  stays consistent. Copy reframed around focus sessions ("Start a focus
  session"), not calendar days.
- **Project aliases are removed** (backend + any frontend editing UI + Alembic
  migration dropping the alias table/columns). Their only consumer was the
  stripped inbox/AI matching.
- **Project task views default to kanban** — hard default, no sticky
  per-project persistence; list stays one toggle away.
- **`/tasks` page stays as-is** (flat list/board with full filters). The
  dashboard swimlanes serve a different purpose; no duplication concern.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Slices (one PR each, squash-merged on green CI)

### Slice 1 — Remove project aliases

- [ ] Alembic migration dropping the alias storage (review autogen; verify
      upgrade/downgrade round-trip).
- [ ] Remove alias fields from `schemas/projects.py`, alias handling from
      `services/projects.py` / `services/trash.py` / `routes_projects.py`,
      and the model columns in `db/models.py`.
- [ ] Remove any frontend alias display/edit surface and types.
- [ ] Doc pass: `README.md` schema/section mentions.

### Slice 2 — Today → Focus rename (+ project board default)

- [ ] Backend: rename today service/endpoint/schemas to focus; tests follow.
- [ ] Frontend: `features/today/` → `features/focus/`, nav label, `/focus`
      route with `/today` redirect, `api/today.ts` → `api/focus.ts`,
      `types/today.ts` → `types/focus.ts`; session-framed copy.
- [ ] Flip the project detail task view default to kanban (likely
      `useTaskUrlState` / project detail view); list remains a toggle.
- [ ] Doc pass: `README.md` / `CLAUDE.md` "Today" mentions become "Focus".

### Slice 3 — Dashboard → swimlane board

- [ ] New swimlane board component in `features/dashboard/` (new component,
      not a `KanbanBoard` retrofit — but reuse `TaskCard`, the status-change
      hooks, and the `is_blocked` move rule).
- [ ] Lane header: project name (link), open count, status tone; collapsed
      state for empty/quiet projects. Per-lane Done toggle using the
      completed-tasks archive fetch.
- [ ] Signal strip: overdue / blocking / due-today counts, clickable filters.
- [ ] Delete the replaced dashboard surfaces: metric cards, workload bars,
      projects-overview table, hero copy (+ their CSS).
- [ ] Verify drag interactions with the `verifier-browser` skill (jsdom can't
      exercise pointer drags).
- [ ] Doc pass: `README.md` dashboard description.

---

## Out of scope for this epic

- Anything agent-related (llama.cpp, MCP, tools, RAG) — Phase 2 in `TODO.md`.
- Restyling `/tasks` or the Focus algorithm itself — rename/reframe only.
- The deferred tasks-table cleanup (`review_status` / `confidence` /
  `assignee_hint`) — separate item in `TODO.md`.

## Definition of done for the epic

All three slices merged; `./test.sh` and CI green; opening the app lands on
the swimlane board; `/today` redirects to `/focus`; no alias code, routes,
columns, or doc mentions remain; project task views open on the kanban.
