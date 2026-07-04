# CLAUDE.md

This file is the project constitution. Read it at the start of every session and
follow it. If a request conflicts with these rules, raise the conflict before
acting.

## What this project is

A local-first project and task management web app with AI-assisted task capture.
See `README.md` for the architecture and plan; `TODO.md` for the backlog;
`CURRENT.md` for the checked-out focus; `DONE.md` for the changelog. This file is
rules of engagement, not the plan.

## Prime directives

1. **The app owns the logic. AI returns structured suggestions, nothing more.**
   Workflows, validation, and persistence are Python. The model produces JSON
   that Python decides what to do with. Never let an AI call write to the
   database or decide a control-flow branch without a Python guard.

2. **Never call a model provider directly from workflow code.** All model calls
   go through `ai/gateway.py`. If you're tempted to `import ollama` outside
   `ai/providers/`, stop and use the gateway.

3. **Validate every model output with Pydantic before it touches anything
   else.** If validation fails, log the raw output, save it to
   `ai_training_examples` as a failure case, and surface the error. No
   best-effort parsing of bad JSON, no silently returning an empty list.

4. **Capture training data on every correction.** When a user edits or rejects
   an AI suggestion, write the original input, the full original model output,
   and the corrected output to `ai_training_examples`. This is the most valuable
   thing the app produces. Treat it like accounting data.

## Scope discipline

- **Ship coherent, related work together; don't sprawl.** A chunk can bundle a
  feature with the shared components or polish it naturally exercises, but keep
  diffs reviewable and don't speculatively build unrelated backlog items.
- **Work from `TODO.md` / `CURRENT.md`.** If a request pulls in something well
  outside the current focus, flag it rather than silently expanding scope.
- **The "Do not build yet" list in README.md is binding.** No custom models,
  calendar sync, auth, Celery, or vector DB. If asked, push back.

## Code rules

- **Python 3.11+, SQLAlchemy 2.0 typed syntax** (`Mapped[str]`,
  `mapped_column(...)`). Not legacy declarative, not SQLModel.
- **Pydantic v2** for all schemas, including model I/O in `ai/schemas.py`.
- **Alembic for every schema change** — `alembic revision --autogenerate`, then
  review the generated file before applying. Never edit the schema without a
  migration.
- **Soft deletes only.** User-facing tables have `deleted_at`; queries filter it
  via the service-layer helper, not sprinkled ad-hoc. (`activity_events` and
  `eval_runs` are append-only exceptions.)
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

## AI subsystem rules

- **Prompts live in `ai/prompts/*.md`**, not in Python string literals — the
  Settings UI edits them at runtime.
- **Profiles live in `ai/profiles.yaml`**; code reads a profile by name. Don't
  hardcode model names or temperatures in workflow code.
- **`response_mode: json_schema`** unless the profile explicitly says `text`
  (free-form text is for summaries only). Watch out: fields the model must
  always emit need **no Pydantic default**, or json_schema/Ollama will omit them
  and you silently get `None`.
- **Idempotency on inbox processing.** Same input hash → return the existing
  inbox item, don't re-extract.
- **Every workflow has an eval case** in the matching `ai/evals/*_cases.yaml`.
  Add or update cases when you add or change a workflow; the harness must pass.

## Network & Discord rules

- The Discord bot is a **separate process** that calls the API over HTTP,
  authenticated by `BACKEND_SHARED_SECRET` in env — no tokens in code or git.
- Default bind is `127.0.0.1`, but **LAN exposure via `API_HOST=0.0.0.0` is an
  intentional, supported mode** (single-user trusted LAN). Settings writes must
  stay localhost-only, and Ollama-calling routes stay rate-limited — preserve
  both when touching routes.

## Dependencies

- **Ask before adding a dependency** — wait for confirmation. Prefer the
  standard library over a package for a 10-line helper. No experimental or
  unmaintained packages.

## Working with the user

- **Plan mode first for non-trivial work** (more than ~2 files or a new concept).
- **Small, reviewable diffs.** If you're about to produce 500+ lines across many
  files in one go, stop and break it up.
- **State assumptions inline**: "Assumed X because Y — change if wrong."
- **Push back when something seems wrong.** Code-review-level honesty, not
  yes-manning. Don't apologize reflexively — acknowledge, fix, move on.

## Definition of done for a slice

1. The vertical path works end-to-end manually (UI → API → DB → UI).
2. At least one happy-path backend test (pytest); the user handles frontend
   tests later.
3. Structured logs with request IDs.
4. If it touches AI: eval case + Pydantic validation.
5. If it touches the schema: Alembic migration committed.
6. `README.md` updated if setup steps, dev commands, schema, or status changed.

Not done, even if it seems done: unrun migrations; a model call that skipped the
gateway "because it was simpler" (revert it); validation that fails silently; a
dependency added without asking (revert, then ask).

## Re-read when context is unclear

`README.md` · `TODO.md` / `CURRENT.md` · `ai/profiles.yaml` · `ai/prompts/` ·
this file
