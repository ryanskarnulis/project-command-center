# CLAUDE.md

This file is the project constitution. Read it at the start of every session and
follow it. If a request conflicts with these rules, raise the conflict before
acting.

## What this project is

A local-first project and task management web app — a simple, boring, reliable
core (projects, tasks, Focus, search, trash, dashboard) with a local agent
(llama.cpp + tools + MCP + retrieval) built on top of it. See `README.md`
for the architecture; `TODO.md` for the backlog; `CURRENT.md` for
the checked-out focus; `DONE.md` for the changelog. This file is rules of
engagement, not the plan.

## Commands

```bash
./main.sh                 # bootstrap env/deps, migrate, start backend + frontend
./test.sh                 # backend pytest/ruff/mypy + frontend Vitest/lint/build
./scripts/backup_db.sh    # online snapshot of data/app.db → data/backups/
```

After intentionally bumping a backend dependency, regenerate the lock:
`cd backend && .venv/bin/python -m pip freeze --exclude-editable > requirements.lock`.

The deployed instance runs via `docker compose` (see `README.md`); `./main.sh`
is dev-only. Don't run both against the same `data/` directory at once.

For frontend changes whose surface is the rendered page — especially
pointer-drag interactions that jsdom/Vitest can't exercise — verify with the
`verifier-browser` skill (`.claude/skills/verifier-browser`), not just tests.

## Git workflow

- **Never commit or push directly to `main`.** For every change: create a
  branch (`feat/<slug>`, `fix/<slug>`, `chore/<slug>`, `docs/<slug>`), commit,
  push, and open a PR with `gh pr create`.
- PRs are **squash-merged once CI is green**. GitHub's native auto-merge and
  branch protection are unavailable (private repo on the Free plan), so after
  opening a PR run `gh pr checks --watch` and, when green,
  `gh pr merge --squash`. Never merge with failing or pending checks.
- CI (`.github/workflows/ci.yml`) mirrors `./test.sh`: backend pytest, ruff,
  and mypy; frontend Vitest, lint, and build. Run `./test.sh` before pushing.
- **Eval gate:** before merging any prompt, model, or loop change, run the
  opt-in agent eval harness
  (`cd backend && PCC_AGENT_EVALS=1 .venv/bin/pytest tests/test_agent_evals.py -v -s`);
  the recorded baseline in `docs/agent-design.md` must not regress. Default
  `pytest` skips it — the evals hit the real model.

## Prime directives

1. **The service layer is the only write path.** UI routes, API clients, and
   the agent's tools are all peers: every mutation goes through
   `services/`, gets validated, respects soft deletes, and lands in
   `activity_events`. Never let anything — route handler, agent tool, script —
   write around it.

2. **The agent inherits rule 1**: it acts exclusively through tools
   backed by the service layer, every action auditable in `activity_events`
   and undoable via the trash. No hard deletes from the agent, ever. Model
   outputs are validated with Pydantic at the boundary — no best-effort
   parsing of bad JSON.

3. **Local-first stays load-bearing.** The agent runs on llama.cpp on this
   machine; retrieval stays in-process (FTS5 first, `sqlite-vec` only if
   needed). No cloud model providers, no external vector DB.

## Scope discipline

- **Ship coherent, related work together; don't sprawl.** A chunk can bundle a
  feature with the shared components or polish it naturally exercises, but keep
  diffs reviewable and don't speculatively build unrelated backlog items.
- **Work from `TODO.md` / `CURRENT.md`.** If a request pulls in something well
  outside the current focus, flag it rather than silently expanding scope.
- **The "Do not build yet" list in README.md is binding.** No multi-user auth,
  Celery, external vector DB, or cloud model providers. If asked, push back.

## Code rules

- **Python 3.11+, SQLAlchemy 2.0 typed syntax** (`Mapped[str]`,
  `mapped_column(...)`). Not legacy declarative, not SQLModel.
- **Pydantic v2** for all schemas.
- **Alembic for every schema change** — `alembic revision --autogenerate`, then
  review the generated file before applying. Never edit the schema without a
  migration.
- **Soft deletes only.** User-facing tables have `deleted_at`; queries filter it
  via the service-layer helper, not sprinkled ad-hoc. (`activity_events` is an
  append-only exception.)
- **Structured logging with `structlog`.** Request ID bound to the logger on
  every request; every workflow log line carries it. No bare `print()` or
  field-less `logging.info()`.
- **Type hints on every signature**, return types included. `mypy --strict`
  should pass eventually; don't make it harder.
- **One responsibility per module.** If a function is doing two things, split it.

## Frontend rules

- **React + Vite + TypeScript, strict mode.** No `any` without a `// TODO` and a
  reason.
- **API calls go through `src/api/`.** Components consume hooks; hooks call the
  API layer.
- **Feature folders, not type folders.** A feature's components, hooks, and
  types live together in `features/<name>/`.
- **No state library.** React state + context. If you think there's a real
  reason for one, raise it first.

## Network rules

- Default bind is `127.0.0.1`, but **LAN exposure via `API_HOST=0.0.0.0` is an
  intentional, supported mode** (single-user trusted LAN). Keep the rate-limit
  module; the agent messages endpoint depends on it.

## Dependencies

- **Ask before adding a dependency** — wait for confirmation. Prefer the
  standard library over a package for a 10-line helper. No experimental or
  unmaintained packages.

## Working with the user

- **State assumptions inline**: "Assumed X because Y — change if wrong."
- **Push back when something seems wrong.** Code-review-level honesty, not
  yes-manning. Don't apologize reflexively — acknowledge, fix, move on.

## Definition of done for a slice

1. The vertical path works end-to-end manually (UI → API → DB → UI).
2. At least one happy-path backend test (pytest); the user handles frontend
   tests later.
3. Structured logs with request IDs.
4. If it deletes a feature: its `README.md`/`CLAUDE.md` sections, routes,
   services, frontend feature folder, env vars, and tables (migration) all go
   in the same PR.
5. If it touches the schema: Alembic migration committed.
6. `README.md` updated if setup steps, dev commands, schema, or status changed.

Not done, even if it seems done: unrun migrations; validation that fails
silently; a dependency added without asking (revert, then ask); a "removal"
that left doc sections, env vars, or dead config behind.

## Re-read when context is unclear

`README.md` · `TODO.md` / `CURRENT.md` · this file
