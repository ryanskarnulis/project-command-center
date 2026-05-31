# Sprint 2 — Inbox, Model Gateway, Extraction, Review Queue

> Goal: the full AI loop works end-to-end — paste messy text → extract candidates →
> review → accept/reject → corrections saved as training data. This is the most important
> sprint. Slice is done when: UI → API → workflow → DB → UI works manually, happy-path
> pytest passes (gateway mocked), evals run against the real model, logs carry request IDs
> from POST → extraction → validation → candidate creation, migration committed, README updated.

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

## Design decisions (locked for this sprint)
- Candidate tasks have **nullable `project_id`** + an `inbox_item_id` FK. Accept only flips
  status to `accepted`; project assignment is Sprint 4 (auto-matching). No project picked at accept.
- Extraction model is **`gemma4:e2b`** via Ollama (already pulled locally).
- Review is a **batch** endpoint `POST /api/inbox/{id}/review` that applies all decisions and
  writes **one** `ai_training_examples` row (full input + full output + corrected output).
  This intentionally supersedes the README's per-candidate `PATCH/DELETE` wording for atomic capture.
- Only `task_extraction` is exercised. `project_matching`/`summary` profiles exist in YAML but are
  not called; their prompt files are not created this sprint.

## Carried over (already done — do not rebuild)
- [ ] `services/common.py` `active()`/`soft_delete()` helpers — reuse for the new models.
- [ ] `Task.status` already has `candidate`/`accepted`/`rejected`/`done` — no enum change.
- [ ] `httpx` + `pyyaml` already in `pyproject.toml`; `ollama_base_url` already in `config.py`.

## Backend — models & migration
- [x] `db/models.py` — add `InboxItem` (raw_text, input_hash, source enum `web|discord`,
      summary|None, project_hint|None, needs_review default True, processed_at|None,
      model_output_json|None, model_name|None) and `AITrainingExample` (task_name, input_text,
      model_output_json, corrected_output_json|None, accepted, model_profile, model_name).
      Both subclass `Base, TimestampMixin, SoftDeleteMixin`.
- [x] `db/models.py` — `Task`: make `project_id` nullable, add `inbox_item_id` FK (nullable),
      add `confidence: float | None` and `assignee_hint: str | None`; add `candidates` relationship
      on `InboxItem`.
- [x] Alembic migration `"inbox, training examples, candidate tasks"`: create the 2 tables;
      `op.batch_alter_table('tasks')` to drop `project_id` NOT NULL + add the new columns/FK.
      Confirm `render_as_batch=True` in `alembic/env.py`. **Review generated file**, then `upgrade head`. Commit.

## Backend — AI subsystem (the gateway is the most important decision)
- [ ] `ai/__init__.py`, `ai/providers/__init__.py`, `ai/workflows/__init__.py` (package scaffolding).
- [ ] `ai/schemas.py` — Pydantic v2 `ExtractedTask`, `ExtractionOutput`, `ExtractionInput`
      (matches the README task-extraction JSON: summary, project_hint|null, tasks[], needs_review).
- [ ] `ai/profiles.yaml` — `task_extraction`, `project_matching`, `summary` (model `gemma4:e2b`,
      temps/tokens/response_mode per README).
- [ ] `ai/prompts/extract_tasks.md` — extraction system prompt (plain markdown, not a Python string).
- [ ] `ai/providers/base.py` — `BaseProvider` ABC, typed `complete(...) -> str`.
- [ ] `ai/providers/ollama.py` — `OllamaProvider` via httpx to `/api/chat`
      (`format=<schema>`, `options.temperature`, `num_predict`, `stream=False`). No `import ollama`.
- [ ] `ai/gateway.py` — load profile by name + its prompt file, route to the provider, return raw text.
      Workflows call only the gateway.
- [ ] `ai/workflows/extract_tasks.py` — idempotent: if processed/has candidates, return them; else
      build input (+ today) → gateway → `ExtractionOutput.model_validate_json` → create candidate Tasks
      (`status=candidate`, `project_id=None`, `inbox_item_id` set), persist summary/hint/needs_review,
      set `processed_at`. On `ValidationError`: log raw + write failure training row + surface error.
- [ ] `ai/evals/extraction_cases.yaml` — 5 hand-written cases with `expect` assertions.
- [ ] `ai/evals/run_evals.py` — `python -m app.ai.evals.run_evals`: run cases vs the real model,
      validate, print pass/fail, exit non-zero on failure.

## Backend — services
- [ ] `services/inbox.py` — `hash_text` (SHA-256, stdlib), idempotent `create_inbox_item`
      (same active hash → return existing, no new row), `get_inbox_item`/`list_inbox_items`,
      `list_candidates(inbox_item_id)`. Inbox only — no extraction/matching logic here.
- [ ] `services/training_data.py` — `record_example(...)` (stdlib `json`), used by the failure path
      and by `/review`.
- [ ] `services/tasks.py` — extend `create_task` for `project_id: int | None`, `inbox_item_id`,
      `confidence`, `assignee_hint` (defaults keep existing callers working).

## Backend — API
- [ ] `schemas/inbox.py` — `InboxCreate`, `InboxRead`, `ReviewDecision` (task_id, action, edits?),
      `ReviewRequest`, `ReviewResult`.
- [ ] `schemas/tasks.py` — `TaskRead`: `project_id: int | None`, add `inbox_item_id`, `confidence`,
      `assignee_hint`.
- [ ] `api/routes_inbox.py` — `POST /api/inbox` (idempotent), `POST /api/inbox/{id}/process`
      (runs workflow; 422 on validation failure), `GET /api/inbox`, `GET /api/inbox/{id}`,
      `GET /api/inbox/{id}/candidates`, `POST /api/inbox/{id}/review` (apply decisions; write ONE
      training row; return result). structlog with request IDs throughout.
- [ ] `main.py` — include `routes_inbox.router` in `api_router`.

## Backend — tests (mock the gateway)
- [ ] `tests/test_inbox.py` — idempotency: same text twice → one row, same id.
- [ ] `tests/test_extract_workflow.py` — happy path (canned valid JSON → candidates created,
      idempotent re-run) + validation failure (bad JSON → failure training row + error surfaced).
- [ ] `tests/test_training_data.py` — `record_example` stores full input/output.
- [ ] (optional) `tests/test_routes_inbox.py` — POST → process → review writes statuses + 1 training row.

## Frontend — inbox & review queue
- [ ] `types/inbox.ts` + extend `types/task.ts` (`project_id: number | null`, `inbox_item_id`,
      `confidence`, `assignee_hint`).
- [ ] `api/inbox.ts` — typed wrappers (`createInbox`, `processInbox`, `listInbox`, `getInbox`,
      `getCandidates`, `reviewInbox`) over `apiClient`.
- [ ] `features/inbox/useInbox.ts` — hook (submit text → create → process → candidates; review state).
- [ ] `features/inbox/InboxPage.tsx` — textarea + submit; shows summary/project_hint; renders ReviewQueue.
- [ ] `features/inbox/ReviewQueue.tsx` — lists candidates (title/desc/due/priority/confidence/assignee),
      per-row accept/reject + inline edit, "Submit review" → `reviewInbox`.
- [ ] `routes/AppRoutes.tsx` — add `/inbox`; add a nav link. No `any` without a `// TODO`.

## Done check
- [ ] Manual e2e: `ollama serve` → `python -m app.main` → `npm run dev` → /inbox: paste messy text →
      process → candidates appear → accept some / reject some / edit one → DB shows correct task
      statuses + exactly one `ai_training_examples` row with full input/output/corrected.
- [ ] Idempotency: re-submit the same text → no duplicate inbox item, no duplicate candidates.
- [ ] Failure path: a malformed extraction writes an `ai_training_examples` failure row and surfaces
      the error (no silent empty list).
- [ ] `cd backend && pytest` green (gateway mocked).
- [ ] `python -m app.ai.evals.run_evals` runs the 5 cases against `gemma4:e2b` and prints pass/fail.
- [ ] Logs show `request_id` across POST /inbox → process → validate → candidate creation.
- [ ] Migration committed; README sprint status + `TASKS.md` Sprint 2 checkboxes updated.
