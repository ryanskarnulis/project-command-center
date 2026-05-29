# Sprint 1 — Projects & Tasks CRUD

> Goal: create/read/update/delete projects and tasks through the API and basic
> React pages. No AI yet. Slice is done when: UI → API → DB → UI works manually,
> happy-path pytest passes, logs carry request IDs, migration committed, README updated.

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

## Carried over from Sprint 0 (already done — do not rebuild)
- [x] `backend/app/db/session.py` — engine + `get_db` dependency (built in Sprint 0)
- [x] `backend/app/db/models.py` — `Base(DeclarativeBase)` exists

## Backend — models & migration
- [x] `backend/app/db/models.py` — `TimestampMixin` (created_at, updated_at) + `deleted_at` column;
      `Project` (id, name, description|None) and `Task` (id, project_id FK, title, description|None,
      status enum candidate|accepted|rejected|done default 'accepted', priority enum
      low|medium|high|urgent default 'medium', due_date|None). SQLAlchemy 2.0 typed
      (`Mapped[...]`, `mapped_column(...)`).
- [x] Alembic migration: `alembic revision --autogenerate -m "projects and tasks"`, review the
      generated file, then `alembic upgrade head`. Commit the migration.

## Backend — services (soft-delete baked in)
- [x] `backend/app/services/common.py` — shared helper that filters `deleted_at IS NULL`
- [x] `backend/app/services/projects.py` — list/get/create/update/soft-delete, using the helper
- [x] `backend/app/services/tasks.py` — list (by project)/get/create/update/soft-delete + mark-done

## Backend — API
- [x] `backend/app/schemas/projects.py` — `ProjectCreate`, `ProjectUpdate`, `ProjectRead` (Pydantic v2)
- [x] `backend/app/schemas/tasks.py` — `TaskCreate`, `TaskUpdate`, `TaskRead` (Pydantic v2)
- [x] `backend/app/api/routes_projects.py` — GET list, GET one, POST, PATCH, DELETE (soft)
- [x] `backend/app/api/routes_tasks.py` — GET list (by project), GET one, POST, PATCH, DELETE (soft)
- [x] `backend/app/main.py` — include both routers under `/api`
- [x] `backend/app/main.py` — add `CORSMiddleware`; `backend/app/config.py` — `cors_origins`
      setting (default `["http://localhost:5173","http://127.0.0.1:5173"]`)

## Backend — tests
- [x] Happy-path pytest for `services/projects.py` (create → get → soft-delete hidden from list)
- [x] Happy-path pytest for `services/tasks.py` (create under project → mark done → soft-delete)

## Frontend — setup
- [x] `npm install react-router-dom` (approved)
- [x] `src/types/project.ts`, `src/types/task.ts` — TS types mirroring the `*Read` schemas
- [x] `src/api/projects.ts`, `src/api/tasks.ts` — typed wrappers over `apiClient`
- [x] `src/routes/` — router with `/projects` and `/projects/:id/tasks`; `App.tsx` renders it

## Frontend — features
- [ ] `src/features/projects/` — `useProjects` hook + project list page + create form
- [ ] `src/features/tasks/` — `useTasks` hook + task list page (scoped to project) + create form
      + mark-done + delete. Hooks call `src/api/`, components consume hooks.

## Done check
- [ ] End-to-end manual test: create project → create task → mark done → soft-deleted project
      disappears from the list
- [x] Logs show structured output with request IDs across the new routes
- [ ] Migration committed; README sprint status + `TASKS.md` Sprint 0/1 checkboxes updated
