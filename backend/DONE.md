
---

## Cleanup & Hardening Sprint
> Goal: reduce silent-failure risk, close type-safety gaps, make builds reproducible, and eliminate known test brittleness. No new features.

### Code correctness
- [x] **De-duplicate training capture** — extracted `_write_training_examples(db, item, accepted)` shared by `review_inbox` and `_finalize_inbox` in `services/review.py`, so the two prime-directive-#4 paths can't silently diverge.
- [x] **Validate `project_id` on task update + SQLite FK enforcement** — `PATCH /api/tasks/{id}` now `_ensure_project`-guards a supplied `project_id`; `db/session.py` issues `PRAGMA foreign_keys = ON` via a dialect-gated `connect` listener on both app and test engines.
- [x] **Audit AI workflow validation paths** — every `ai/workflows/` failure mode (bad JSON, Pydantic rejection, gateway timeout) logs raw output, writes a failure training row, and surfaces a proper error code; added `GatewayError` (→ 502) so an Ollama outage is an upstream failure, never an unhandled 500.
- [x] **Narrow the summary route's exception handling** — `get_project_summary` in `routes_ai.py` now catches `gateway.GatewayError` (→ 502) instead of bare `Exception`, matching the other model routes; a genuine bug in the summary path surfaces as a 500 instead of being mislabeled an Ollama outage.

### Type safety
- [x] **Minor Python type/import tidy** — `Callable` imported from `collections.abc`; dropped the `# type: ignore[arg-type]` in `services/review.py` via a `cast` to the `Literal`.
- [x] **Audit TypeScript `any`** — swept `src/` for `: any` / `as any`, replaced with typed shapes or annotated per CLAUDE.md rules.

### Reproducibility
- [x] **Pin backend dependencies** — added version pins in `pyproject.toml` + a committed `requirements.lock`.

### Test reliability
- [x] **Migration smoke test** — `test_migrations.py` runs `alembic upgrade head` against a fresh SQLite file and asserts it completes, keeping the migration chain valid without forcing every test through Alembic.

### Security / ops
- [x] **Expose gateway internals as public helpers** — promoted `gateway.local_profiles_path()` / `prompts_dir()` / `load_raw_merged()`; `settings.py` no longer reaches into private gateway attributes.
- [x] **Rate limiting on model-calling endpoints** — added an in-process per-IP sliding-window limiter (`api/rate_limit.py`) on `POST /api/discord/inbox` and `GET /api/projects/{id}/summary`, tunable via `.env`.
- [x] **Make rate-limit coverage consistent across model-calling routes** — extended the per-IP limiter to the two remaining Ollama-invoking web routes, `POST /api/inbox/{id}/process` and `POST /api/tasks/{id}/break-down`, each with its own `Settings` cap (`rate_limit_inbox_process_per_min`, `rate_limit_breakdown_per_min`, default 20) so the limiter can't be bypassed via the web path; added a suite-wide autouse limiter reset in `conftest.py`.

### Dropped (sprint goal met)
- mypy `--strict` full-backend pass — directional goal per CLAUDE.md, not a sprint-closer; left to ratchet over time.
- Root-causing the `TaskDetailPage` / `ProjectDetailPage` parallel-run flakes — test-only, pre-existing, frontend tests owned by the user; not worth blocking the sprint.
