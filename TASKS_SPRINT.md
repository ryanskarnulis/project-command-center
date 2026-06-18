# Sprint 9e — Projects Tab UX Overhaul

> Goal: bring the Projects tab up to par with the Sprint 8–9d Tasks/Inbox polish.
> Today `/projects` is a bare `<ul>` + inline create form: no detail page (clicking a
> project jumps straight to `/projects/:id/tasks`), no counts/progress/status, no
> search/sort, and aliases buried in an edit modal. This sprint adds a clickable card
> list, an inline-editable **detail hub** at `/projects/:id`, per-project
> counts/progress/status, search/sort, and consistent empty/loading/error states —
> all by reusing the existing Tasks/Inbox patterns and endpoints.
>
> Ship as **6 small chunks** (CLAUDE.md: small reviewable diffs, one slice at a time),
> stopping after each for manual review/test/commit. Detail-page model chosen by the
> user: **Hub + keep board** — card → `/projects/:id` overview; the existing
> `/projects/:id/tasks` board stays, reached via a "View all tasks" link.

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

## Ground rules (read first)

- **Frontend-only. No backend change, no new/changed API route, no Alembic migration.**
  Every approved item reuses an existing endpoint. If you reach for a backend change,
  stop — `GET /api/projects/{id}` · `/tasks` · `/activity` · `/summary` and alias CRUD
  already exist, and counts/status derive client-side from `listAllTasks()` +
  `listCompletedTasks()` (exactly what the dashboard already does).
- React + Vite + TS strict; no `any` without a `// TODO` + reason. API calls go through
  `src/api/`; components consume hooks. Plain CSS in `src/index.css` — **reuse existing
  classes** (`.task-card`, `.task-detail*`, `.task-filters`, the pill styles); add
  `.project-*` only where genuinely new. Feature folders, not type folders.
- Per chunk: `cd frontend && npm run test && npm run build` green.
- One-line commit per chunk at the chunk stop, in the running style:
  `<letters>: Sprint 9e - <chunk> (...)`.

## Request → chunk map (the 10 approved asks)

| Improvement | Chunk |
| --- | --- |
| 1. `ProjectCard` — polished clickable cards | 3 |
| 2. Project detail hub `/projects/:id` (inline-editable) | 1 |
| 3. `ProjectFormModal` create/edit (retire inline form + `ProjectEditModal`) | 3 |
| 4. Task counts + progress bar on cards | 4 |
| 5. Derived status badge (Clear/On Track/Due Soon/At Risk/Blocked) | 4 |
| 6. AI summary on the detail page | 2 |
| 7. Activity feed on the detail page | 2 |
| 8. Search projects | 5 |
| 9. Sort projects | 5 |
| 10. Polish (empty/loading/error, confirm-delete, breadcrumbs) | 6 |

## Reuse map (model on / import — do NOT reinvent)

- **Detail page pattern** → `features/tasks/TaskDetailPage.tsx`: `savePatch`/`saveState`
  (`idle|saving|saved|error`), per-field drafts saved `onBlur`, `.task-detail-header`,
  `.breadcrumb`, `.task-detail-actions`, `.save-state`, `.task-hero`,
  `.task-detail-grid`, `.task-detail-panel`, `.task-section-heading`, 404→redirect.
- **Card** → `features/tasks/TaskCard.tsx` (`actions?` slot, `<Link>`, badges).
- **Modal** → `features/tasks/TaskFormModal.tsx` (create|edit discriminated union) +
  `components/Modal.tsx`.
- **Status logic** → extract `projectStatus(tasks, openCount)` + `Tone` from
  `features/dashboard/DashboardPage.tsx` (≈ lines 137–147) into
  `utils/projectStatus.ts`; import in both the dashboard and the card.
- **Activity** → `features/projects/ActivityFeed.tsx` + `useProjectActivity.ts` (exist).
- **API/hook** → `api/projects.ts` (`getProject`, `create`, `update`, `remove`, alias
  CRUD, `getProjectActivity`) + `features/projects/useProjects.ts`. Add a
  `getProjectSummary` wrapper (`GET /api/projects/{id}/summary`, 502-safe) if missing.
- **Tasks** → the scoped fetch `TasksPage` uses for `/projects/:id/tasks`, plus
  `listAllTasks()` / `listCompletedTasks()` for stats.
- **Utils/CSS** → `utils/dates.ts` (`dueStatus`, `formatDueDate`, `compareTasks`);
  pills `.status-pill.*` `.priority-pill.*` `.due.*` `.estimate` `.source-pill`;
  filters `.task-filters` `.task-search-field` `.task-filter-grid`.

---

## Chunk 1 — Project detail hub skeleton + route  *(#2 core)*

**Files (new):** `features/projects/ProjectDetailPage.tsx`, `ProjectDetailPage.test.tsx`.
**Files (modify):** `routes/AppRoutes.tsx` (+ `/projects/:id`), `ProjectsPage.tsx`
(relink list `<Link>` `/projects/:id/tasks` → `/projects/:id`).

- [x] Breadcrumb `← Projects`; inline-editable **name** + **description** via
      `updateProject` (`savePatch`/`saveState` pattern); client-side empty-name guard.
- [x] "View all tasks →" link to `/projects/:id/tasks`.
- [x] Tasks section: the project's open tasks as `TaskCard`s (reuse the scoped fetch);
      loading/error/empty states (separate `tasksLoading`/`tasksError` so a task-fetch
      failure doesn't blank the page).
- [x] 404 → redirect to `/projects` (`ApiError.status === 404`, mirrors `TaskDetailPage`).
- [x] Reuse `.task-detail*` CSS (no new classes added).
- [x] Tests: name + tasks + View-all link render; inline name save; blank-name guard.
      `ProjectDetailPage.test.tsx` 3/3; full suite 89/89; `tsc -b && vite build` green.
- Non-regression: bare list still renders; clicking now lands on the working hub.

## Chunk 2 — Detail hub sections: AI summary + activity + aliases  *(#6, #7, aliases home)*

**Files (modify):** `ProjectDetailPage.tsx`, `ProjectDetailPage.test.tsx`.
(`getProjectSummary` + the `ProjectSummary` type already existed in `api/dashboard.ts`
/ `types/dashboard.ts` — imported those; no new wrapper/type needed.)

- [x] AI summary: "Summarize" button → `getProjectSummary` (`GET /api/projects/{id}/summary`);
      loading + 502-safe error ("Summary unavailable — is Ollama running?"); renders the
      prose + model name.
- [x] Activity: mount existing `ActivityFeed` (`activityKey` bumped on project edits).
- [x] Aliases: list + add + remove (reuse `listAliases`/`createAlias`/`deleteAlias`) as
      a detail section — gives aliases their permanent home before `ProjectEditModal` is
      retired in Chunk 3. Same labels as the modal (`Add alias`, `Add`, `Remove alias X`).
- [x] Tests: summarize calls API + renders text; alias add + remove; activity mounts.
      `ProjectDetailPage.test.tsx` 6/6; full suite 92/92; build green.
- Non-regression: `ProjectEditModal` still reachable from the list until Chunk 3.

## Chunk 3 — `ProjectCard` + list rework + `ProjectFormModal`; retire `ProjectEditModal`  *(#1, #3)*

**Files (new):** `features/projects/ProjectCard.tsx`, `ProjectCard.test.tsx`,
`features/projects/ProjectFormModal.tsx`, `ProjectFormModal.test.tsx`.
**Files (modify):** `ProjectsPage.tsx`, `ProjectsPage.test.tsx`, `index.css`.
**Files (remove):** `ProjectEditModal.tsx` + `ProjectEditModal.test.tsx` (aliases now on the hub).

- [ ] `ProjectCard`: `<Link to="/projects/:id">`, name, description, protected badge,
      optional `stats?` prop (consumed in Chunk 4), `actions?` slot (Edit, Delete/Protected).
- [ ] `ProjectsPage`: replace `<ul>` with a card grid; replace inline form with
      **"+ New project"** → `ProjectFormModal` (create). Card Edit → `ProjectFormModal` (edit).
- [ ] `ProjectFormModal`: discriminated-union `create | edit` like `TaskFormModal`
      (name required, description optional), `saving`/error states.
- [ ] CSS: `.project-card`, `.project-grid` (reuse `.task-card` look; minimal new rules).
- [ ] Tests: card renders + links; "+ New project" opens modal + creates; Edit opens edit mode.
- Non-regression: aliases reachable on the hub (Chunk 2); create/edit/delete intact.

## Chunk 4 — Counts + progress + status badge  *(#4, #5)*

**Files (new):** `utils/projectStatus.ts`, `utils/projectStatus.test.ts`.
**Files (modify):** `DashboardPage.tsx` (import the extracted util — no behavior change),
`ProjectsPage.tsx` (load `listAllTasks()` + `listCompletedTasks()`, build per-project
stats, pass `stats` to cards), `ProjectCard.tsx`, `ProjectDetailPage.tsx` (same stats in
the hero), `index.css` (reuse `.workload-bar`/progress styles), tests.

- [ ] Extract `projectStatus(tasks, openCount)` + `Tone` to the util; refactor the
      dashboard to use it (visually unchanged).
- [ ] Stats helper: group open tasks (count, `is_blocked`, `due_date` →
      blocked/overdue/due-soon) + done count; `progress = done / (open + done)`.
- [ ] Card: `N open · M done`, a progress bar, and the status pill.
- [ ] Tests: `projectStatus` mapping; card shows counts/progress.

## Chunk 5 — Search + sort  *(#8, #9)*

**Files (modify):** `ProjectsPage.tsx`, `ProjectsPage.test.tsx`, `index.css`
(reuse `.task-filters`/`.task-search-field`).

- [ ] Search: case-insensitive over name + description (client-side).
- [ ] Sort: Name / Most open tasks / Recently updated / Recently created (uses Chunk 4
      stats for "most open tasks"; `updated_at`/`created_at` otherwise).
- [ ] "Clear" affordance when active; distinct empty message when a search hides all.
- [ ] Tests: search narrows; each sort orders; clear restores.

## Chunk 6 — Polish bundle  *(#10)*

**Files (modify):** `ProjectsPage.tsx`, `ProjectDetailPage.tsx`,
`ProjectCard.tsx`/`ProjectFormModal.tsx`, `index.css`, tests.

- [ ] Consistent `.empty-state` / `.page-loading` + `role="alert"` errors across both
      pages (match Tasks/Inbox).
- [ ] Confirm-before-delete (`window.confirm`, like the inbox "Dismiss note").
- [ ] Breadcrumb consistency (`← Projects`; section headings via `.task-section-heading`).
- [ ] Tests: confirm-delete only deletes on confirm; empty/loading states render.

---

## Verification

**Per chunk (definition of done):**
1. `cd frontend && npm run test` — new + existing Vitest suites green.
2. `cd frontend && npm run build` — strict TS build passes (no stray `any`).
3. Tick the chunk's boxes here; stop for manual review/commit.

**End-to-end manual (per README dev commands; Ollama needed only for Chunk 2's summary):**
- **1/2/3:** `/projects` shows cards; click → `/projects/:id` hub with editable
  name/description (persists on blur) + the project's tasks; "View all tasks" →
  `/projects/:id/tasks` (unchanged board); "+ New project" creates; Edit updates.
- **4/5:** each card shows `open · done`, a progress bar, and the right status pill;
  the dashboard Projects Overview is unchanged.
- **6:** "Summarize" returns prose (or a clean 502 message if Ollama is down).
- **7:** activity feed + alias add/remove work on the hub.
- **8/9:** search narrows the list; each sort reorders; clear restores.
- **10:** deleting asks for confirmation; empty/loading/error states match Tasks.

## Bookkeeping

- Tick each chunk's boxes here as it lands.
- README update only if a documented flow/route changes — this sprint adds **no**
  routes/schema, so the only doc-worthy change is the new `/projects/:id` hub page and
  projects being card-based; note it (or defer to a Sprint 9e summary line) at the end.
- `TASKS.md` (master list) currently has uncommitted edits; leaving it untouched unless
  asked. One-line commit per chunk at the chunk stop.
