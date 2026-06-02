
## 9. Prioritized Improvement Plan

### Critical

#### Atomic extraction and review persistence

Status:

Completed.

What changed:

- Low-level services now avoid implicit commits.
- Routes and workflow orchestration now own transaction boundaries.
- Extraction and review now commit their related writes atomically.
- Rollback tests were added for the highest-risk paths.

Where:

- `backend/app/ai/workflows/extract_tasks.py`
- `backend/app/services/review.py`
- `backend/app/services/tasks.py`
- `backend/app/services/training_data.py`
- `backend/app/services/activity.py`

#### Database-backed inbox idempotency

Status:

Completed.

What changed:

- `InboxItem.input_hash` is protected by an active-row partial unique index.
- Alembic migration fails loudly if duplicate active hashes already exist.
- `create_inbox_item()` catches `IntegrityError` and returns the existing active row after a concurrent insert collision.
- Soft-deleted inbox rows no longer block future same-text submissions.
- Focused tests cover normal idempotency, soft-delete resubmission, and a two-session stale-read race.

Where:

- `backend/app/db/models.py`
- `backend/app/services/inbox.py`
- `backend/app/alembic/versions/20260601_8a3f2d1c9b0e_active_inbox_hash_unique.py`
- `backend/tests/test_inbox.py`


### High Impact

#### Fix hidden accepted tasks after project deletion

Status:

Completed.

What changed:

- Added protected default `General` project support.
- Added migration `20260601_4f2c8b7d0a1e_default_general_project.py`.
- Rehomed active tasks to `General` when a project is deleted.
- Rejected deletion of protected projects.
- Added global task listing through `GET /api/tasks`.
- Added frontend `/tasks` navigation from the main nav and dashboard count.
- Added focused tests for idempotent default creation, rehoming behavior, protected deletion, global task visibility, and dashboard count reachability.

Where:

- `backend/app/services/projects.py`
- `backend/app/api/routes_projects.py`
- `backend/app/api/routes_tasks.py`
- `backend/app/services/dashboard.py`
- `frontend/src/features/tasks/TasksPage.tsx`
- `frontend/src/features/dashboard/DashboardPage.tsx`
- `frontend/src/features/projects/ProjectsPage.tsx`
- `backend/tests/test_projects.py`
- `backend/tests/test_tasks.py`
- `backend/tests/test_routes_ai.py`

#### Make Discord processing match web processing

Status:

Completed.

What changed:

- Discord capture now calls `match_workflow.match_inbox_item(db, item)` after successful extraction.
- Matching failures are logged and non-fatal, matching the web inbox route's behavior.
- The Discord response schema is unchanged.
- Focused tests cover successful suggestion persistence and non-fatal match failure.

Where:

- `backend/app/api/routes_discord.py`
- `backend/tests/test_routes_discord.py`

#### Protect settings write routes

Status:

Completed.

What changed:

- Added a localhost-only guard for settings mutation routes.
- Applied it to profile `PATCH`, prompt `PUT`, and eval `POST` handlers.
- Kept the read-only settings routes public.
- Added route tests for localhost allow, LAN reject, and no-side-effect behavior.
- Documented the guard in `config.py`, `.env.example`, and the README.

Where:

- `backend/app/api/routes_settings.py`
- `backend/tests/test_routes_settings.py`
- `backend/app/config.py`
- `backend/.env.example`
- `README.md`

#### Add server-side pending inbox endpoint

Status:

Completed.

What changed:

- Added `GET /api/inbox/pending` with a bounded `limit` query parameter.
- Moved the processed-but-unreviewed filter and newest-first ordering into the backend service layer.
- Updated the frontend inbox hook to load pending items directly from the new endpoint.
- Added focused route test cases for filtering, ordering, and limiting.

Verification note:

- Test execution is intentionally deferred to the final user-run test pass.

Where:

- `backend/app/services/inbox.py`
- `backend/app/api/routes_inbox.py`
- `frontend/src/api/inbox.ts`
- `frontend/src/features/inbox/useInbox.ts`
- `backend/tests/test_routes_inbox.py`

Original issue:

The frontend loads all inbox items and filters pending items client-side.

Why it matters:

This will degrade with real inbox history.

Where:

- `backend/app/services/inbox.py`
- `backend/app/api/routes_inbox.py`
- `frontend/src/features/inbox/useInbox.ts`

Recommended fix:

Add query parameters or a dedicated endpoint for processed, unreviewed inbox items with a sensible limit.

Estimated effort:

Small to Medium

Risk level:

Low

### Medium Impact

#### Optimize dashboard aggregation

Status:

Completed.

What changed:

- Replaced per-project task counting with one grouped aggregate query.
- Active projects with zero open tasks still appear in the dashboard response.
- Batched recent inbox project resolution instead of resolving each inbox row one at a time.
- Accepted reviewed task destinations now remain the source of truth, with active suggested projects used only as a fallback.
- Soft-deleted projects, tasks, and inbox items are excluded from dashboard aggregation where appropriate.
- Added focused dashboard tests for counts, empty projects, recent inbox ordering, deleted rows, resolution precedence, suggestion fallback, tie handling, and bounded query count.

Verification note:

- Test execution is intentionally deferred to the final user-run test pass.

Where:

- `backend/app/services/dashboard.py`
- `backend/tests/test_routes_ai.py`

#### Add input validation for blank strings

Status:

Completed.

What changed:

- Added shared Pydantic v2 request-string helpers for non-blank required text and optional stripped/null text.
- Applied request validation to project names, project aliases, task titles, inbox raw text, Discord raw text, and review edit titles.
- Normalized blank optional project/task/review text fields to `null`.
- Kept read/response schemas unchanged.
- Added focused route tests for trimmed valid input, whitespace-only rejection, optional blank normalization, inbox hash-after-trim behavior, Discord validation before extraction, and review edit normalization.

Verification note:

- User reported the test suite passes.

Where:

- `backend/app/schemas/common.py`
- `backend/app/schemas/projects.py`
- `backend/app/schemas/tasks.py`
- `backend/app/schemas/inbox.py`
- `backend/app/schemas/discord.py`
- `backend/tests/test_projects.py`
- `backend/tests/test_tasks.py`
- `backend/tests/test_routes_inbox.py`
- `backend/tests/test_routes_discord.py`

#### Add frontend smoke tests for the inbox review flow

Status:

Completed.

What changed:

- Added Vitest, jsdom, Testing Library, and jest-dom frontend test tooling.
- Added `npm run test` and `npm run test:watch` scripts.
- Wired Vitest through the Vite config with a shared test setup file.
- Added accessible labels to repeated inbox review controls so tests can drive the UI the same way a user would.
- Added a page-level smoke test for loading a pending inbox item, loading candidates, editing one candidate, rejecting another, submitting review, and verifying the submitted review payload plus UI reset.

Verification note:

- Test execution is intentionally deferred to the final user-run test pass.

Where:

- `frontend/package.json`
- `frontend/package-lock.json`
- `frontend/vite.config.ts`
- `frontend/tsconfig.app.json`
- `frontend/src/test/setup.ts`
- `frontend/src/features/inbox/`
- `frontend/src/features/inbox/InboxPage.test.tsx`
- `frontend/src/features/inbox/ReviewQueue.tsx`

### Low Priority / Nice to Have

#### Replace frontend README boilerplate

Issue:

`frontend/README.md` is still the Vite template README.

Why it matters:

It is mildly confusing for project onboarding.

Where:

- `frontend/README.md`

Recommended fix:

Replace with frontend-specific dev notes or remove it if the root README is canonical.

Estimated effort:

Small

Risk level:

Low

#### Restore or document missing sprint files

Issue:

Sprint 4 and Sprint 6 details live in `TASKS.md`, but the matching sprint files are missing.

Why it matters:

The docs say to inspect all sprint files in order, but the series has gaps.

Where:

- root docs

Resolved:

`TASKS_SPRINT_4.md` and `TASKS_SPRINT_6.md` now exist alongside the other sprint docs.

Estimated effort:

Small

Risk level:

Low

#### Replace deprecated 422 status constant

Status:

Completed.

What changed:

- Replaced the deprecated FastAPI 422 status constant in the two route handlers that emitted the warning.

Where:

- `backend/app/api/routes_discord.py`
- `backend/app/api/routes_inbox.py`

## 10. Suggested Next Refactor

Now that the frontend inbox review smoke test is in place, the next recommended refactor is replacing the Vite boilerplate in `frontend/README.md`.

This should come next because the remaining high- and medium-impact correctness work is complete, and the current frontend README is mildly confusing for onboarding. The root README already carries the broader project context, so the frontend README should either become a concise frontend-specific development note or be removed if the root README is intended to stay canonical.

Recommended slice:

1. Decide whether `frontend/README.md` should exist as a frontend-specific note or be removed in favor of the root README.
2. If keeping it, replace the Vite template content with concise commands for install, dev server, build, lint, tests, and relevant environment variables.
3. Point readers back to the root README for backend setup and full-project workflows.
4. Keep it short so it does not drift from the root documentation.
