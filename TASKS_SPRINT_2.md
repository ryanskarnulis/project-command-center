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
- [x] `services/common.py` `active()`/`soft_delete()` helpers — reuse for the new models.
- [x] `Task.status` already has `candidate`/`accepted`/`rejected`/`done` — no enum change.
- [x] `httpx` + `pyyaml` already in `pyproject.toml`; `ollama_base_url` already in `config.py`.

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
- [x] `ai/__init__.py`, `ai/providers/__init__.py`, `ai/workflows/__init__.py` (package scaffolding).
      (also `ai/evals/__init__.py` so `python -m app.ai.evals.run_evals` works.)
- [x] `ai/schemas.py` — Pydantic v2 `ExtractedTask`, `ExtractionOutput`, `ExtractionInput`
      (matches the README task-extraction JSON: summary, project_hint|null, tasks[], needs_review).
- [x] `ai/profiles.yaml` — `task_extraction`, `project_matching`, `summary` (model `gemma4:e2b`,
      temps/tokens/response_mode per README).
- [x] `ai/prompts/extract_tasks.md` — extraction system prompt (plain markdown, not a Python string).
- [x] `ai/providers/base.py` — `BaseProvider` ABC, typed `complete(...) -> str`.
- [x] `ai/providers/ollama.py` — `OllamaProvider` via httpx to `/api/chat`
      (`format=<schema>`, `options.temperature`, `num_predict`, `stream=False`). No `import ollama`.
- [x] `ai/gateway.py` — load profile by name + its prompt file, route to the provider, return raw text.
      Workflows call only the gateway.
- [x] `ai/workflows/extract_tasks.py` — idempotent: if processed/has candidates, return them; else
      build input (+ today) → gateway → `ExtractionOutput.model_validate_json` → create candidate Tasks
      (`status=candidate`, `project_id=None`, `inbox_item_id` set), persist summary/hint/needs_review,
      set `processed_at`. On `ValidationError`: log raw + write failure training row + surface error.
- [x] `ai/evals/extraction_cases.yaml` — 5 hand-written cases with `expect` assertions.
- [x] `ai/evals/run_evals.py` — `python -m app.ai.evals.run_evals`: run cases vs the real model,
      validate, print pass/fail, exit non-zero on failure. (5/5 pass against `gemma4:e2b`.)

## Backend — services
- [x] `services/inbox.py` — `hash_text` (SHA-256, stdlib), idempotent `create_inbox_item`
      (same active hash → return existing, no new row), `get_inbox_item`/`list_inbox_items`,
      `list_candidates(inbox_item_id)`. Inbox only — no extraction/matching logic here.
- [x] `services/training_data.py` — `record_example(...)`, used by the failure path
      and by `/review`. (Built alongside the AI subsystem — the workflow's failure path needs it.)
- [x] `services/tasks.py` — extend `create_task` for `project_id: int | None`, `inbox_item_id`,
      `confidence`, `assignee_hint` (defaults keep existing callers working).

## Backend — API
- [x] `schemas/inbox.py` — `InboxCreate`, `InboxRead`, `ReviewDecision` (task_id, action, edits?),
      `ReviewRequest`, `ReviewResult`.
- [x] `schemas/tasks.py` — `TaskRead`: `project_id: int | None`, add `inbox_item_id`, `confidence`,
      `assignee_hint`.
- [x] `api/routes_inbox.py` — `POST /api/inbox` (idempotent), `POST /api/inbox/{id}/process`
      (runs workflow; 422 on validation failure), `GET /api/inbox`, `GET /api/inbox/{id}`,
      `GET /api/inbox/{id}/candidates`, `POST /api/inbox/{id}/review` (apply decisions; write ONE
      training row; return result). structlog with request IDs throughout.
- [x] `main.py` — include `routes_inbox.router` in `api_router`.

## Backend — tests (mock the gateway)
- [x] `tests/test_inbox.py` — idempotency: same text twice → one row, same id.
- [x] `tests/test_extract_workflow.py` — happy path (canned valid JSON → candidates created,
      idempotent re-run) + validation failure (bad JSON → failure training row + error surfaced).
- [x] `tests/test_training_data.py` — `record_example` stores full input/output.
- [x] (optional) `tests/test_routes_inbox.py` — POST → process → review writes statuses + 1 training row.

## Frontend — inbox & review queue
- [x] `types/inbox.ts` + extend `types/task.ts` (`project_id: number | null`, `inbox_item_id`,
      `confidence`, `assignee_hint`).
- [x] `api/inbox.ts` — typed wrappers (`createInbox`, `processInbox`, `getCandidates`,
      `reviewInbox`) over `apiClient`. (`listInbox`/`getInbox` omitted — single-flow UI doesn't
      use them; no inbox history list this sprint.)
- [x] `features/inbox/useInbox.ts` — hook (submit text → create → process → candidates; review
      state; surfaces the 422 extraction-validation failure, no silent empty list).
- [x] `features/inbox/InboxPage.tsx` — textarea + submit; shows summary/project_hint/needs_review;
      renders ReviewQueue; shows ReviewResult + "New capture".
- [x] `features/inbox/ReviewQueue.tsx` — lists candidates (title/desc/due/priority/confidence/assignee),
      per-row accept/reject + inline edit of all `ReviewEdit` fields, "Submit review" → `reviewInbox`
      (sends only changed fields as `edits`).
- [x] `routes/AppRoutes.tsx` — add `/inbox`; minimal shared nav bar in `App.tsx`. No `any`.

## Done check
- [x] Manual e2e: `ollama serve` → `python -m app.main` → `npm run dev` → /inbox: paste messy text →
      process → candidates appear → accept some / reject some / edit one → DB shows correct task
      statuses + one `ai_training_examples` row with full input/output/corrected. — **Verified via
      DB** (inbox #5): 4 accepted / 1 rejected; edited title "Image compression pass"→"Image
      compression" applied to the task AND captured in corrected output; rejected task excluded
      from corrected output; confidence varied (1.0/0.85/0.9/0.6) in real data.
- [x] Idempotency: re-submit the same text → no duplicate inbox item, no duplicate candidates.
      — **Verified**: 3 review attempts on the same note all resolved to inbox #5 (one row).
- [x] **Finding — re-review is unguarded → FIXED.** Re-reviewing an already-reviewed inbox item
      re-flipped statuses and wrote duplicate, contradictory `ai_training_examples` rows (inbox #5
      → 3 rows). Fix: added `InboxItem.reviewed_at` (migration `def4d6c65e01`); `review_inbox` now
      raises `AlreadyReviewedError` if already reviewed and stamps `reviewed_at` on success; route
      returns **409**; frontend shows "already reviewed" on a re-pasted note. Test
      `test_review_twice_conflicts`. Stale rows #1/#2 for inbox #5 soft-deleted (kept final #3).
- [x] Failure path: a malformed extraction writes an `ai_training_examples` failure row and surfaces
      the error (no silent empty list). — workflow raises `ValidationError` + records a failure row
      (`test_extract_validation_failure_records_training_row`); route maps it to **422**
      (`test_process_malformed_extraction_422_and_training_row`).
- [x] `cd backend && pytest` green (gateway mocked). — 13 passed.
- [x] `python -m app.ai.evals.run_evals` runs the cases against `gemma4:e2b` and prints pass/fail.
      — now **7 cases, 7/7** stable at `--repeat 5` after the recall/confidence prompt fix.
- [x] Logs show `request_id` across POST /inbox → process → validate → candidate creation.
      — `RequestIDMiddleware` binds it via `contextvars`; `merge_contextvars` folds it into every
      structlog event. Verified it reaches threadpool-run sync handlers + the workflow they call
      (`test_request_id_propagates_through_request`): all process-request log lines share one id.
- [x] Migration committed; README sprint status + `TASKS.md` Sprint 2 checkboxes updated.
      — inbox/training/candidate migration committed in `ff547d8`; `reviewed_at` migration
      `def4d6c65e01` staged for this commit. README marks Sprint 2 **[DONE]**; `TASKS.md` Sprint 2
      frontend boxes checked (batch `/review` noted as superseding per-candidate PATCH/DELETE).

## Extraction quality (2026-05-31 follow-up — recall & confidence)
- [x] Root-caused missed tasks: `max_tokens: 1024` truncated multi-task JSON; prompt conflated
      "low stakes" with "no task". Fixed both (max_tokens→2048; "low stakes ≠ no task" directive).
- [x] Prompt: recall directive + few-shot example + full-range confidence guidance.
- [x] Eval harness: 2 hard messy-multi-task cases + `confidence_varies`/`low_confidence_present`;
      `run_evals` gained `--model`/`--profile`/`--repeat`; gateway gained benchmark-only `model_override`.
- [x] Benchmarked e2b vs e4b vs 26b: e2b 7/7 (kept — fastest); e4b too slow; 26b times out.
