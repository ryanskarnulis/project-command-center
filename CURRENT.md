# Sprint 9f — Trash Tab UX Overhaul

> Goal: bring `/trash` up to par with the Sprint 8–9e Tasks/Inbox/Projects polish.
> Today `TrashPage` is a bare `<main>` with three `<ul>` lists and plain "Restore"
> buttons — no cards, no icons, no item context, no timestamps, no search/sort, no
> nav count, no shared loading/empty/error states, and `deleted_at` isn't even
> exposed by the API. Everything else now uses `TaskCard`/`ProjectCard`, lucide
> icons, `.task-filters`, `.page-loading`/`.empty-state`, status pills, and confirms.
>
> Ship as **5 small chunks** (CLAUDE.md: small reviewable diffs, one slice at a
> time), stopping after each for manual review/test/commit. The two destructive /
> backend-touching pieces (expose `deleted_at`, permanent delete) are isolated into
> their own chunks so the safe visual parity lands first and #10 stays reviewable
> on its own.

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

## The 10 approved asks → chunk map

| # | Improvement | Chunk |
| --- | --- | --- |
| 1 | Card layout + lucide icons + section headings | 2 |
| 2 | "Deleted X ago" relative timestamps | 1 (BE) + 2 (UI) |
| 3 | Rich item context badges (project/priority/due, source pill, project counts) | 2 |
| 4 | Search box | 3 |
| 5 | Type filter (Projects / Tasks / Inbox) | 3 |
| 6 | Empty / loading / error state parity | 2 |
| 7 | Trash count badge in the nav | 4 |
| 8 | Bulk "Restore all" per section | 4 |
| 9 | Restore feedback (inline confirmation + rehome/409 notices) | 4 |
| 10 | Permanent delete (purge) per item + "Empty trash" | 5 |

## Ground rules (read first)

- **Slice discipline:** Chunk 1 is a 3-field schema add (+ frontend types). Chunk 5
  is the only chunk with new backend service logic + routes. Chunks 2–4 are
  **frontend-only** and reuse existing endpoints (`GET /api/trash`, the three
  `POST .../restore` routes).
- React + Vite + TS strict; no `any` without a `// TODO` + reason. API calls go
  through `src/api/`; components consume hooks. Plain CSS in `src/index.css` —
  **reuse existing classes** (`.task-card`, `.page-loading`, `.empty-state`,
  `.task-filters`, `.task-search-field`, the pill styles, `.source-pill`,
  `.status-pill.*`, `.priority-pill.*`, `.due.*`); add `.trash-*` only where new.
- Backend: Python 3.11+, SQLAlchemy 2.0 typed, Pydantic v2, structlog with the
  request-bound logger. Soft-delete helpers live in `services/common.py`.
- Per chunk: `cd frontend && npm run test && npm run build` green; for chunks that
  touch the backend, `cd backend && ./.venv/bin/pytest` green too.
- One-line commit per chunk at the chunk stop, in the running style:
  `<letters>: Sprint 9f - <chunk> (...)`.

## ⚠️ #10 — conflict flagged and accepted

CLAUDE.md prime directive: **"Soft deletes only … training data references them —
don't actually delete rows."** A true purge contradicts this. The user approved it
after the flag. It is de-risked by one fact verified in the code:
**`ai_training_examples` has NO foreign keys** to `tasks`/`inbox_items`/`projects`
— it stores full `input_text` + `model_output_json` by design — so purging a
trashed row **cannot orphan training data**. Chunk 5 still:
- only ever purges rows that are **already soft-deleted** (in trash); never an
  active row (404 otherwise);
- cleans the real FK edges explicitly (see Chunk 5);
- requires an explicit per-item / empty-trash confirm in the UI.
No Alembic migration: purge is DML (row deletion), not a schema change.

## Reuse map (model on / import — do NOT reinvent)

- **Cards** → `features/tasks/TaskCard.tsx` (badges, `actions?` slot) for trashed
  tasks; `features/projects/ProjectCard.tsx` (`stats?` prop) for trashed projects.
  Inbox items: a small inline card reusing `.task-card` + `.source-pill`.
- **States** → `.page-loading`, `.empty-state`, `role="alert" class="error"`
  (same as `ProjectsPage`/`TasksPage` after Sprint 9e Chunk 6).
- **Filters** → `.task-filters` / `.task-search-field` (search + type filter, Chunk 3).
- **Status/stats** → `utils/projectStatus.ts` `buildProjectStats` for project cards.
- **Dates** → `utils/dates.ts`. **Add `formatRelative(iso)` here** ("3 days ago")
  — no relative formatter exists yet (ActivityFeed uses raw `toLocaleString()`);
  the new helper gets a unit test and can be reused by ActivityFeed later.
- **Restore plumbing** → `features/trash/useTrash.ts` already wraps
  `restoreProject/Task/Inbox` with the inbox-409 handling; extend it, don't rewrite.
- **Backend restore pattern** → `routes_{projects,tasks,inbox}.py` `*/restore`
  + `services/common.py` (`deleted()`, `restore()`); Chunk 5 purge routes mirror
  the shape (`DELETE /{id}/purge`).

---

## Chunk 1 — Backend: expose `deleted_at` (+ frontend types)  *(#2 core)*

**Files (modify):** `backend/app/schemas/projects.py`, `backend/app/schemas/tasks.py`,
`backend/app/schemas/inbox.py`, `frontend/src/types/{project,task,inbox}.ts`,
`backend/tests/test_routes_trash.py`.

- [x] Add `deleted_at: datetime | None = None` to `ProjectRead`, `TaskRead`,
      `InboxRead` (`from_attributes=True` already set; populates from the ORM
      `SoftDeleteMixin.deleted_at`; serializes `null` for active rows — backward
      compatible for every other consumer).
- [x] Mirror in the frontend types: optional `deleted_at?: string | null` on
      `Project`, `Task`, `InboxItem`.
- [x] pytest: `GET /api/trash` items carry a non-null `deleted_at`; an active
      project/task `GET` carries `null`.
- Non-regression: existing trash + list tests still green (additive field only).

## Chunk 2 — Card layout + icons + context badges + states  *(#1, #3, #6, #2 UI)*

**Files (modify):** `features/trash/TrashPage.tsx`, `features/trash/TrashPage.test.tsx`,
`utils/dates.ts` (+ `utils/dates.test.ts`), `index.css`.

- [x] Section headings with lucide icons (`FolderX` / `Trash2` / `Inbox`) + per-
      section count, e.g. `Projects (2)`.
- [x] Trashed **tasks** render as `TaskCard` (badges: project, priority, due) with a
      Restore action in the `actions` slot; **projects** as `ProjectCard`
      (`stats` from `buildProjectStats`); **inbox** as a small `.task-card` with the
      `.source-pill` + summary/`raw_text` snippet.
- [x] Each card shows **"Deleted {formatRelative(deleted_at)}"** (new `dates.ts`
      helper; unit-tested).
- [x] States: `.page-loading` (loading), `.empty-state` with icon + copy when trash
      is empty, `role="alert" class="error"` for the error path.
- [x] CSS: add `.trash-*` only where a card/section truly needs it; otherwise reuse.
- [x] Tests: cards render per type; deleted-time label renders; empty/loading/error
      states render; restore buttons still wired.
- Non-regression: restore still works via the unchanged `useTrash` actions.
- Flagged + fixed: shared cards are `<Link>`s; trashed cards wrap them in a
      capture-phase `preventDefault` (`NoNav`) so they don't navigate to a deleted
      item's 404'ing detail page. Also enabled Testing Library `cleanup` in
      `src/test/setup.ts` (no `globals: true`, so auto-cleanup never registered).

## Chunk 3 — Search + type filter  *(#4, #5)*

**Files (modify):** `features/trash/TrashPage.tsx`, `TrashPage.test.tsx`, `index.css`
(reuse `.task-filters` / `.task-search-field`).

- [x] Search: case-insensitive over each item's display label (project name, task
      title, inbox summary/`raw_text`).
- [x] Type filter: All / Projects / Tasks / Inbox (client-side; hides empty
      sections). Reuse the `.task-filters` bar; filter bar only renders when trash
      is non-empty.
- [x] "Clear" appears when a search term or non-`All` filter is active (resets both);
      distinct "No items match your search." message when a search hides everything.
- [x] Tests: search narrows + clear restores; type filter shows only that section;
      no-match message renders.

## Chunk 4 — Nav count + bulk restore + restore feedback  *(#7, #8, #9)*

**Files (modify):** `components/AppShell.tsx`, `features/trash/useTrash.ts`,
`features/trash/TrashPage.tsx`, `TrashPage.test.tsx`, `AppShell.test.tsx`,
`index.css`. **Files (new, maybe):** `features/trash/TrashCountContext.tsx`.

- [x] **Nav count (#7):** lightweight count beside the Trash link in `AppShell`.
      `TrashCountContext`/provider (new `features/trash/TrashCountContext.tsx`,
      wrapped in `App.tsx`) fetches `getTrash()` once and exposes `count` +
      `refresh()`; `useTrash` calls `refresh()` on every reload so the badge stays
      live. Context default is a no-op so `AppShell` renders with no provider (its
      standalone test stays green). Badge hidden at `0`.
- [x] **Bulk restore (#8):** "Restore all" button per non-empty section
      (`restoreAll(kind, items)` in `useTrash`); iterates ids through the existing
      per-item restore, tolerating inbox 409s, reloading once, and reporting
      restored-vs-skipped in the notice.
- [x] **Restore feedback (#9):** new transient `notice` channel on `useTrash`;
      single restores name the item, tasks-section shows an up-front "Restored tasks
      return to General" hint + the notice repeats it; inbox-409 still messages via
      the error channel.
- [x] Tests: badge shows the summed count and hides at 0 (`AppShell.test`); "Restore
      all" restores a section; success notice names the item; bulk 409 path still
      messages correctly.
- Flagged + fixed: the post-action reload's refetch was clearing an error set by a
      409/failure (its `.then` called `setError(null)`). Removed that clobber —
      reloads only fire from actions that already reset error/notice at their start,
      so the action's outcome now survives the refetch.

## Chunk 5 — Permanent delete (purge) + Empty trash  *(#10 — see ⚠️ above)*

**Files (new):** none (routes go in existing routers).
**Files (modify, BE):** `services/projects.py`, `services/tasks.py`,
`services/inbox.py`, `services/common.py` (a `hard_delete` helper),
`routes_projects.py`, `routes_tasks.py`, `routes_inbox.py`, `routes_trash.py`,
`tests/test_routes_trash.py`.
**Files (modify, FE):** `api/{projects,tasks,inbox}.ts`, `api/trash.ts`,
`features/trash/useTrash.ts`, `TrashPage.tsx`, tests.

- [x] `common.hard_delete(db, obj)` — guard: refuse if `obj.deleted_at is None`
      (only purge rows already in trash; raise so the route returns `409`).
- [x] **Per-entity FK cleanup** (verified against `db/models.py`):
      - **Task:** delete its `task_dependencies` rows (both `task_id` and
        `depends_on_task_id`); the cascade-soft-deleted subtree is already in trash,
        so purge the whole soft-deleted subtree together (children share the parent's
        deletion) to avoid a dangling `parent_task_id`.
      - **Project:** hard-delete its `aliases` and any **soft-deleted** tasks still
        pointing at it (active tasks were rehomed to General on delete, so they don't
        reference it). Never purge the protected `General` project.
      - **Inbox item:** detach soft-deleted candidate tasks (`inbox_item_id = NULL`)
        or purge them if they're trashed too; don't touch accepted/active tasks.
      - **`ai_training_examples` is left untouched** (no FK; self-contained).
- [x] **Flagged + added (FK enforcement is OFF — no `PRAGMA foreign_keys`, so the
      DB won't cascade; explicit cleanup is mandatory):** project purge also nulls
      `inbox_items.suggested_project_id` and `activity_events.project_id` (the audit
      row survives with the ref cleared) — two FK edges into `projects` the original
      list missed. Task/inbox purge re-fetch-and-skip across subtree cascades.
- [x] Routes mirror restore: `DELETE /api/projects/{id}/purge`,
      `DELETE /api/tasks/{id}/purge`, `DELETE /api/inbox/{id}/purge` (404 if not
      found, 409 if not soft-deleted, 403 for protected `General`); plus
      `DELETE /api/trash` = empty trash (purge all soft-deleted, protected-project
      safe, returns per-kind counts). structlog `*_purged` / `trash_emptied` lines.
- [x] Frontend: per-card "Delete forever" (with `window.confirm`) + an "Empty trash"
      button (confirm, names the total). Wired into `useTrash` + the Chunk 4 count
      refresh; `.trash-danger` button style added.
- [x] pytest: purge removes the row; purge of a non-deleted row → 409; purge of
      `General` → 403; **`ai_training_examples` rows survive a task/inbox purge**;
      FK cleanup leaves no dangling dependency/alias/parent rows + clears the two
      nullable project FKs; empty-trash clears all and is idempotent.

---

## Verification

**Per chunk (definition of done):**
1. `cd frontend && npm run test` — new + existing Vitest suites green.
2. `cd frontend && npm run build` — strict TS build passes (no stray `any`).
3. Chunks 1 & 5: `cd backend && ./.venv/bin/pytest` green.
4. Tick the chunk's boxes here; stop for manual review/commit.

**End-to-end manual (per README dev commands):**
- **1:** `GET /api/trash` items show `deleted_at`; active gets `null`.
- **2:** `/trash` shows cards with icons, context badges, and "Deleted X ago";
  empty/loading/error states match the rest of the app.
- **3:** search narrows; the type filter isolates a section; clear restores.
- **4:** the nav shows a live trash count; "Restore all" empties a section; a
  success notice names what was restored and warns that tasks rehome to General.
- **5:** "Delete forever" (confirm) removes one item permanently; "Empty trash"
  (confirm) clears everything; the `/training` meter / training viewer is
  **unchanged** afterward (training rows survive).

## Bookkeeping

- Tick each chunk's boxes here as it lands; one-line commit per chunk at the stop.
- README update at sprint end: add a `Sprint 9f [DONE]` line (Trash card overhaul +
  the new `deleted_at` field on the three Read schemas + the `DELETE …/purge` and
  `DELETE /api/trash` routes — the only documented-surface changes). No new page
  route (still `/trash`); **no Alembic migration** (purge is DML; schema unchanged).
- `DONE.md` / `TODO.md` (uncommitted, working tree) left untouched unless asked.
