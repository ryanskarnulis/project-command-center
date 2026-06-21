# Sprint 10a — AI "Break this down" (per-task subtask suggestion)

> Labelled **10a**: README reserves Sprint 10 for the gated Unsloth export and
> names this feature Sprint 11 (backlog). It ships first, so 10a avoids renumbering
> the gated work.

## Why this sprint

The north star is collecting `ai_training_examples` until there are 200+, then
training a custom model. We had exactly one correctable AI surface (inbox
extraction). This adds a second: a per-task **Break this down** action that sends a
task's title + description through `ai/gateway.py` and proposes subtasks, surfaced
as `review_status="candidate"` children for the standard review/accept flow. It
reuses the whole extraction pattern (gateway-only call → Pydantic validation →
candidate rows → training capture on correction), so it is low-risk and directly
grows the corpus. It is the README's explicitly-named next backlog feature.

## What shipped

### Slice 1 — schema, profile, prompt, Pydantic I/O
- `tasks.breakdown_output_json` nullable column (Alembic `5b5f79d37b6e`). Holds the
  raw model output on the parent **only between generating subtasks and reviewing
  them**, so the correction (accepted/edited subtasks vs original) can be captured
  to `ai_training_examples` at review time (prime directive #4). Cleared on review.
  - The backlog hoped for "no new schema", but that's unachievable while honoring
    directive #4 — the original output must survive from generate-time to
    review-time. One nullable column is the honest carrier.
- `app/ai/schemas.py` — `BreakdownSubtask` / `BreakdownOutput` / `BreakdownInput`
  (mirrors the extraction schemas; `extra="forbid"`; `confidence` has no default
  per the required-nullable model-field rule).
- `break_down_task` profile in `profiles.yaml` (gemma4:e2b, json_schema) +
  `ai/prompts/break_down_task.md` (decompose within scope; atomic/vague handling).

### Slice 2 — workflow, review capture, routes
- `app/ai/workflows/break_down_task.py` — mirrors `extract_tasks.py`: idempotent
  (existing candidate children or a pending `breakdown_output_json` short-circuit
  without a model call), gateway call, Pydantic validation, training-failure
  capture + re-raise on invalid output, candidate children via existing
  `create_task(parent_task_id=...)` (project inherited from parent).
- `app/services/breakdown.py` — `review_breakdown`: approve flips a candidate child
  to accepted (with edits), dismiss soft-deletes it; once no candidates remain,
  writes one `ai_training_examples` correction row (full input/output/corrected)
  and clears `breakdown_output_json`.
- `app/api/routes_tasks.py` — `POST /api/tasks/{id}/break-down` (422 on invalid
  model output) + `POST /api/tasks/{id}/breakdown/review` (409 when nothing pending).
- New schemas in `schemas/tasks.py` (`SubtaskEdit` / `SubtaskDecision` /
  `BreakdownReviewRequest` / `BreakdownReviewResult`).
- Eval suite: `ai/evals/breakdown_cases.yaml` + `run_breakdown_evals.py` (exposes
  `run()`), registered in `services/settings.py` `_EVAL_SUITES`. 6/6 on gemma4:e2b.

### Slice 3 — frontend (TaskDetailPage)
- `api/tasks.ts` — `breakDownTask(id)` + `reviewBreakdown(id, decisions)`; types in
  `types/task.ts`.
- `TaskDetailPage` — "Break this down" button in the Subtasks heading; suggested
  candidates render as `TaskCard`s with Approve / Dismiss actions; approving keeps
  them, dismissing removes them. Uses the page's existing save-state/error feedback.

## Verification
- `alembic upgrade head` clean (one nullable column).
- `pytest` green (236 + new breakdown/route tests).
- `mypy --strict` clean on the new/changed backend modules.
- `npm run build` (`tsc -b && vite build`) clean; `TaskDetailPage.test.tsx` 5/5.
- `run_breakdown_evals` 6/6 on gemma4:e2b.
- Follow-up frontend cleanup fixed the stale `DashboardPage.test.tsx` /
  `ProjectDetailPage.test.tsx` expectations and the React 19 lint blockers
  (`react-refresh/only-export-components`, `react-hooks/set-state-in-effect`).

## Out of scope
- No auto-accept (every subtask reviewed). No recursive breakdown. No batch
  "break down all". No change to inbox extraction or the command bar.
