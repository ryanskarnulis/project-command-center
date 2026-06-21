# CLAUDE.md

This file is the project constitution. Read it at the start of every session and follow it. If a request conflicts with these rules, raise the conflict before acting.

## What this project is

A local-first project and task management web app with AI-assisted task capture. See `README.md` for the full plan. This file is rules of engagement, not the plan.

## Prime directives

1. **The app owns the logic. AI returns structured suggestions, nothing more.** Workflows are Python. Validation is Python. Persistence is Python. The model produces JSON that Python decides what to do with. Never let an AI call write to the database directly. Never let an AI call decide a control flow branch without a Python guard.

2. **Never call a model provider directly from workflow code.** All model calls go through `ai/gateway.py`. If you're tempted to `import ollama` outside `ai/providers/`, stop and use the gateway.

3. **Validate every model output with Pydantic before it touches anything else.** If validation fails, log the raw output, save it to `ai_training_examples` as a failure case, and surface the error. Do not "best effort" parse bad JSON.

4. **Capture training data on every correction.** Whenever a user edits or rejects an AI suggestion, write the original input, the original model output, and the corrected output to `ai_training_examples`. This is the most valuable thing the app produces. Treat it like accounting data.

## Scope discipline

- **Ship coherent, related work together; don't sprawl.** The core is stable, so a chunk of work can bundle a feature with the shared components or polish it naturally exercises — it doesn't have to be a single isolated slice. Still keep the diffs reviewable (see "Small, reviewable diffs" below) and don't speculatively build unrelated backlog items because they "might" be next.
- **Work from the backlog in `TODO.md`.** The "Next sprint" entry at the top is what's queued; the rest is unprioritized, theme-grouped backlog. If a request pulls in something well outside the current focus, flag it rather than silently expanding scope.
- **The "Do not build yet" list in README.md is binding.** No custom models, no calendar sync, no auth, no Celery, no vector DB. If asked for one of these, push back.

## Code rules

- **Python 3.11+. SQLAlchemy 2.0 typed syntax** (`Mapped[str]`, `mapped_column(...)`). Not the legacy declarative style. Not SQLModel.
- **Pydantic v2** for all schemas, including model I/O schemas in `ai/schemas.py`.
- **Alembic for every schema change.** Never edit the DB schema without generating a migration. `alembic revision --autogenerate -m "..."` then review the generated file before applying.
- **Soft deletes only.** Every user-facing table has `deleted_at: Mapped[datetime | None]`. Queries filter `deleted_at IS NULL` by default. Provide a service-layer helper for this; don't sprinkle the filter everywhere.
- **Structured logging with `structlog`.** Every request has a request ID bound to the logger. Every workflow log line carries it. No bare `print()`. No bare `logging.info()` without structured fields.
- **Type hints on every function signature.** Return types included. `mypy --strict` should pass on the backend eventually; don't actively make this harder.
- **One responsibility per module.** `services/inbox.py` does inbox things. It does not also do project matching. If a function is doing two things, split it.

## Frontend rules

- **React + Vite + TypeScript, strict mode on.** No `any` without a `// TODO` and a reason.
- **API calls go through `src/api/`**, not inline in components. Components consume hooks; hooks call the API layer.
- **Feature folders, not type folders.** `features/inbox/` contains its components, hooks, types. Don't scatter a feature across `components/`, `hooks/`, `types/`.
- **No state library yet.** React state + context is enough. Don't pull in Redux/Zustand/Jotai until there's a real reason. If you think there's a reason, raise it first.
- **No CSS framework yet** unless the user asks. Plain CSS modules or vanilla CSS. Keep it boring.

## AI subsystem rules

- **Prompts live in `ai/prompts/*.md` as plain markdown.** Not in Python string literals. The settings UI will edit these files at runtime — don't hardcode prompt text in code.
- **Profiles live in `ai/profiles.yaml`.** Code reads the profile by name and uses what's there. Don't hardcode model names or temperatures in workflow code.
- **Every model call uses `response_mode: json_schema`** unless the profile explicitly says `text`. Free-form text is for summaries only.
- **Idempotency on inbox processing.** Hash the input text. If the same hash arrives again, return the existing inbox item; don't re-extract.
- **Every workflow has an eval case.** When you add or change a workflow, add at least one case to `ai/evals/extraction_cases.yaml` (or the appropriate file). The eval harness should still pass.

## Discord rules (when we get there)

- **Bot is a separate process.** It calls the FastAPI backend over HTTP on localhost.
- **Bind the API to 127.0.0.1 only** unless explicitly told otherwise. Don't expose the API on 0.0.0.0.
- **Shared secret in env var** for bot → API auth. No tokens in code, no tokens in git.

## Dependencies

- **Ask before adding a dependency.** "Need to add X for Y" — wait for confirmation. Don't silently add packages to `pyproject.toml` or `package.json`.
- **Prefer the standard library.** `datetime`, `pathlib`, `hashlib`, `uuid`, `json` cover a lot. Don't pull in a library for a 10-line helper.
- **No experimental or unmaintained packages.** Check the last release date and open issue count before suggesting something.

## Working with the user

- **Plan mode first for non-trivial work.** If a task touches more than ~2 files or introduces a new concept, present a plan before writing code.
- **Small, reviewable diffs.** Commit-sized chunks. If you're about to produce 500+ lines across many files in one go, stop and break it up.
- **State assumptions inline.** If you guessed at something the user didn't specify, say so in the response: "Assumed X because Y — change if wrong."
- **Push back when something seems wrong.** The user asked for code review-level honesty, not yes-manning. If a request contradicts this file or the README, say so.
- **Don't apologize reflexively.** Acknowledge mistakes, fix them, move on.

## What "done" looks like for a slice

A slice is done when:

1. The vertical path works end-to-end manually (UI → API → DB → UI).
2. There's at least one happy-path test (pytest for backend, the user will handle frontend tests later).
3. Logs show structured output with request IDs.
4. If it touches AI: there's an eval case and Pydantic validation.
5. If it touches the schema: there's an Alembic migration committed.
6. `README.md` is updated if any of these changed: setup steps, dev commands, schema, sprint status.

## Things that are NOT done even if they seem done

- "It works in dev but I haven't run migrations." → Not done.
- "I added the model call but skipped the gateway because it was simpler." → Revert and use the gateway.
- "Validation fails silently and returns an empty list." → Not done. Surface the error.
- "I added a dependency without asking." → Revert. Ask.

## Files you should re-read when context is unclear

- `README.md` — the plan, sprint status, schema overview
- `ai/profiles.yaml` — current model config
- `ai/prompts/` — current prompts
- This file
