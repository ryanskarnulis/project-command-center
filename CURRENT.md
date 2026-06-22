# Sprint 15 — UX Foundation

## Why this sprint

After the security posture slice, the strongest coherent next slice was the UX
foundation group: make navigation safer, make the app shell tell the truth about
local-first behavior, and make task views stable through URLs and browser history.

## What shipped

- Routing now uses React Router's data-router path (`createBrowserRouter` +
  `RouterProvider`) with `AppShell` as the root layout. All existing routes were
  preserved.
- Settings keeps the existing `beforeunload` guard and now also blocks in-app
  navigation while profile/prompt edits are dirty. The blocker uses the existing
  modal style with `Stay` and `Leave without saving`.
- `AppShell` no longer claims unavailable features: the fake focus session,
  disabled notification/search/customize buttons, and fake sync timestamp were
  replaced with honest local workspace/status copy.
- `TasksPage` now syncs filters, sort mode, board/list view, and `new=1` create
  deep links back to query params. Browser back/forward restores task view state.
- `TODO.md`, `DONE.md`, and README sprint status were updated.

## Verification

- Added/updated frontend tests covering data-router route rendering, Settings
  route blocking, shell truthfulness, and task URL sync/history behavior.
- Per user request, tests were **not run** in this environment.

## Out of scope

- No backend routes, schema migration, model/provider changes, or new dependencies.
- No frontend state library or CSS framework.
