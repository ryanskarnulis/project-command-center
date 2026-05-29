# Sprint 0 — Skeleton & Infrastructure

**Current sprint.** Work only on tasks in this file. Do not build Sprint 1 features.

**Done when:**
- `uvicorn app.main:app --reload` starts clean, `/health` responds
- `npm run dev` opens the Vite page
- Alembic is initialized and `alembic current` runs without errors
- structlog request-ID middleware is wired in and visible in logs
- All tasks below are checked off and changes are committed
- pytest runs green from backend/ with at least the health test passing

---

## Context for Claude Code

We are building a local-first project/task management app with AI-assisted task capture.
Read `CLAUDE.md` and `README.md` before touching any file.

Stack for this sprint:
- Python 3.11, FastAPI, pydantic-settings, structlog, Alembic, SQLAlchemy 2.0
- React + Vite + TypeScript (strict mode)
- SQLite at `data/app.db` (relative to repo root)
- Testing: pytest, pytest-asyncio, httpx (test client) — backend only this sprint

Key rules that apply right now:
- No business logic this sprint — skeleton only
- No database models yet — Alembic init only
- Type hints on every function signature
- No `print()` — structlog only
- Ask before adding any dependency not already in `pyproject.toml`

---

## Tasks

### Backend — FastAPI skeleton

- [X] **`backend/app/config.py`**
  - `Settings` class using `pydantic-settings` BaseSettings
  - Fields: `app_env: str`, `database_url: str`, `ollama_base_url: str`
  - Reads from `.env` via `model_config = SettingsConfig(env_file=".env")`
  - Cached singleton: `@lru_cache` on `get_settings()`

- [X] **`backend/app/logging_config.py`**
  - Configure `structlog` with JSON renderer in production, console renderer in dev
  - `RequestIDMiddleware` (Starlette `BaseHTTPMiddleware`) that generates a UUID per request and binds it to the structlog context
  - Export `configure_logging()` and `RequestIDMiddleware`

- [X] **`backend/app/main.py`**
  - Create `FastAPI` app instance
  - Call `configure_logging()` on startup
  - Add `RequestIDMiddleware`
  - Mount an `api_router` (empty for now, ready for Sprint 1 routes)
  - `GET /health` → `{ "status": "ok", "env": settings.app_env }`
  - Bind to `127.0.0.1` in uvicorn config (not `0.0.0.0`)

- [ ] **`backend/app/db/session.py`**
  - Create SQLAlchemy engine from `settings.database_url`
  - `SessionLocal` factory
  - `get_db()` dependency (yields a session, closes on exit)
  - No models yet — just the engine and session wiring

- [ ] **Alembic initialization**
  - `alembic init backend/app/alembic`
  - Edit `alembic.ini`: set `script_location = app/alembic`
  - Edit `alembic/env.py`: import `Base` from `db/models.py` (stub — empty `Base` is fine for now), read `sqlalchemy.url` from `get_settings().database_url`
  - `alembic current` should run without errors (no migrations yet is fine)

- [X] **Smoke test**
  - `cd backend && uvicorn app.main:app --reload`
  - `curl http://127.0.0.1:8000/health` returns `{"status":"ok","env":"development"}`
  - Request-ID appears in log output

### Backend — Test harness (infrastructure only)

- [ ] Add `pytest`, `pytest-asyncio`, `httpx` to backend deps
- [ ] `backend/tests/conftest.py`
  - `client` fixture returning a FastAPI `TestClient`
  - `db_session` fixture using an isolated temp/in-memory SQLite db
  - Override the `get_db` dependency so tests never touch `data/app.db`
- [ ] `backend/tests/test_health.py` — one test asserting `GET /health`
  returns 200 and `{"status":"ok","env":"development"}`
- [ ] `pytest` runs green from `backend/`

### Frontend — Vite scaffold cleanup

- [ ] **Feature folder structure confirmed**
  - `src/features/dashboard/`, `src/features/projects/`, `src/features/tasks/`
  - `src/features/inbox/`, `src/features/settings/`
  - `src/api/`, `src/components/`, `src/routes/`, `src/types/`

- [ ] **`tsconfig.json` (or `tsconfig.app.json`) — strict mode verified**
  - `"strict": true` is present under `compilerOptions`
  - `npm run build` passes with no type errors on the default scaffold

- [ ] **Delete Vite boilerplate**
  - Remove `src/App.css`, `src/assets/react.svg`, placeholder content in `App.tsx`
  - Replace `App.tsx` with a bare `<div>Project Command Center</div>` so the slate is clean

- [ ] **`src/api/client.ts`** — base fetch wrapper
  - `apiClient(path, options)` that prepends `http://127.0.0.1:8000`
  - Throws a typed `ApiError` on non-2xx responses
  - No auth headers yet

- [ ] **Smoke test**
  - `npm run dev` opens the page, shows "Project Command Center"
  - `npm run build` exits 0 with no type errors

---

## How to use this file with Claude Code

**Starting a task:** paste this into Claude Code:
> "I'm working on Sprint 0. Read CLAUDE.md, README.md, and TASKS_SPRINT_0.md.
> I want to implement [specific task from above]. Show me a plan first."

**When you get stuck:** paste the specific task + the error or question. Don't paste the whole file.

**After each task is done:** check it off here and in `TASKS.md`, then commit:
```
git add .
git commit -m "Sprint 0: [what you just built]"
```

Small commits. One task per commit where possible.

---

## Suggested implementation order

Do them in this order — each one sets up the next:

1. `config.py` — everything else imports settings
2. `logging_config.py` — everything else logs
3. `main.py` — wires config + logging, gives you a running server
4. `db/session.py` — engine exists, Alembic can point at it
5. Test harness — conftest + health test ← new
6. Alembic init — schema versioning in place before any models land
7. Frontend folder structure + tsconfig check
8. Delete Vite boilerplate + write `client.ts`
9. Smoke test both ends, commit

---

## Definition of done for this sprint

- [ ] `curl http://127.0.0.1:8000/health` returns 200 with correct JSON
- [ ] Log line shows `request_id` on every request
- [ ] `alembic current` runs without errors
- [ ] `npm run build` exits 0
- [ ] No bare `print()` calls anywhere
- [ ] No `any` in TypeScript without a `// TODO`
- [ ] Committed on main: `git log --oneline` shows Sprint 0 work
- [ ] pytest exits 0 from backend/
