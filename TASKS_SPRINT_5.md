# Sprint 5 — Dashboard & Settings UI

> Goal: a useful overview page, plus a settings panel for tuning AI (prompts, profiles, evals)
> without restarting the backend.
>
> Two vertical slices. **Build Slice A end-to-end and commit before starting Slice B.**
> Status legend: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

Decisions locked for this sprint:
- Per-project summaries are **on-demand** (button), never eager on dashboard load.
- The `summary` profile is `response_mode: text` — no Pydantic output schema; it is the sanctioned
  free-form exception. No training-correction capture (summaries are read-only; there is no
  accept/reject loop to correct).
- Profile edits write to **`backend/app/ai/profiles.local.yaml`** (gitignored), merged over the
  committed `profiles.yaml`. Prompt edits write directly to `ai/prompts/*.md` (already read fresh per
  call by the gateway).

---

## Slice A — Dashboard + project summary [DONE]

### Backend

- [x] `backend/app/ai/prompts/summarize_project.md` — plain-text status summary prompt (no JSON).
- [x] `backend/app/ai/schemas.py` — `SummaryInput` + `SummaryTaskRow` added (mirrors
      `ExtractionInput`/`MatchInput`). No output schema — text mode.
- [x] `backend/app/ai/workflows/summarize_project.py` — `summarize_project_ai(*, project_id,
      project_name, tasks, today) -> str`. structlog bound to `project_id`. No DB writes.
- [x] `backend/app/services/dashboard.py` — `get_overview(db)`: open-task counts (accepted only),
      per-project counts, recent inbox items each paired with a `resolved_project_id` (modal
      project from accepted tasks, falling back to `suggested_project_id` if not yet reviewed).
- [x] `backend/app/schemas/dashboard.py` — `DashboardRead`, `ProjectOpenTasksRow`,
      `ProjectSummaryRead`, `RecentInboxItem` (with `resolved_project_id`, not `suggested_project_id`).
- [x] `backend/app/api/routes_ai.py` — `GET /api/dashboard` (instant) +
      `GET /api/projects/{id}/summary` (502 on Ollama failure, never 500).
- [x] `backend/app/main.py` — router wired in.
- [x] `backend/app/ai/evals/summary_cases.yaml` + `run_summary_evals.py` — 3 cases; `run()`
      function exposed for the future settings eval trigger.
- [x] `backend/tests/test_routes_ai.py` (7 tests) + `test_summary_workflow.py` (2 tests) — 51/51
      suite passing.

### Frontend

- [x] `src/types/dashboard.ts` — `DashboardOverview`, `ProjectOpenTasksRow`, `ProjectSummary`,
      `RecentInboxItem` (with `resolved_project_id`).
- [x] `src/api/dashboard.ts` — `getDashboard()`, `getProjectSummary(projectId)`.
- [x] `src/features/dashboard/useDashboard.ts` — overview on mount; per-project `summarize()`
      tracks loading/error independently so one slow call doesn't block the rest.
- [x] `src/features/dashboard/DashboardPage.tsx` — open-tasks total, per-project rows with
      on-demand **Summarize** button, recent inbox links routing to the project tasks page the item
      actually resolved to (or `/inbox` if unmatched).
- [x] `src/App.tsx` — Dashboard nav link added.
- [x] `src/routes/AppRoutes.tsx` — `/dashboard` route added; `/` redirects to `/dashboard`.

### Slice A "done" check — verified
Dashboard counts render instantly (no model call). Summarize works per-project independently.
Ollama down → 502 returned cleanly, counts still render. Recent inbox items with a matched
project link to that project's tasks page; manually-filed items resolve correctly via accepted
task `project_id` (not the pre-review suggestion). `run_summary_evals` passes (3/3).
`pytest` 51/51. TypeScript build clean.

---

## Slice B — Settings UI (profiles, prompts, eval trigger) [DONE]

### Backend

- [x] `backend/app/ai/profiles.local.yaml` — gitignored overrides file (create empty / via UI). Add it
      to `.gitignore`.
- [x] `backend/app/ai/gateway.py` — load = committed `profiles.yaml` **deep-merged with**
      `profiles.local.yaml` (local wins per-field). Keep `@lru_cache` but add a `reload_profiles()`
      that clears it, called after any write. Prompts already read fresh per call — leave that.
- [x] `backend/app/services/settings.py` — owns settings IO (one responsibility):
      - `list_profiles()` → effective merged profiles (mark which fields are overridden).
      - `update_profile(name, fields)` → validate against the `Profile` model (reject unknown
        profile / bad field / out-of-range temp), write the override to `profiles.local.yaml`, call
        `gateway.reload_profiles()`. Never touch the committed `profiles.yaml`.
      - `list_prompts()` / `get_prompt(name)` / `put_prompt(name, text)` — restricted to existing
        files in `ai/prompts/` (reject path traversal / unknown names).
      - `run_eval(suite)` → invoke the matching eval module's structured runner
        (`task_extraction` / `project_matching` / `summary`); return per-case pass/fail + totals.
        Runs **synchronously** (single-user local app; no Celery per CLAUDE.md). To support this,
        refactor each `run_*.py` to expose a `run() -> results` function with the CLI `main()` as a
        thin wrapper (keeps existing CLI behaviour). `run_summary_evals` already had `run()`;
        added matching `run()` to `run_evals.py` + `run_match_evals.py`.
- [x] `backend/app/schemas/settings.py` — `ProfileRead`/`ProfileUpdate`, `PromptRead`/`PromptUpdate`,
      `EvalCaseResult`/`EvalRunResult`.
- [x] `backend/app/api/routes_settings.py` — new router (tag `settings`):
      - `GET /api/settings/profiles`, `PATCH /api/settings/profiles/{name}`
      - `GET /api/settings/prompts`, `GET /api/settings/prompts/{name}`, `PUT /api/settings/prompts/{name}`
      - `POST /api/settings/evals/{suite}/run`
      Validate everything; surface errors (404/422), never silent best-effort.
- [x] `backend/app/main.py` — `api_router.include_router(routes_settings.router)`.
- [x] Tests: `backend/tests/test_routes_settings.py` (12 tests) — PATCH a profile → effective profile
      changes and `profiles.yaml` on disk is byte-identical; PUT a prompt round-trips; unknown
      prompt/profile, bad field, out-of-range temp, and path traversal rejected; eval-run endpoint
      returns structured results (suite `run()` monkeypatched so no real model call). 63/63 suite.

### Frontend

- [x] `src/types/settings.ts` — `Profile`, `Prompt`, `EvalCaseResult`, `EvalRunResult`.
- [x] `src/api/settings.ts` — wrappers for the six endpoints via `apiClient`.
- [x] `src/features/settings/useSettings.ts` — load profiles + prompt list; save profile, save prompt,
      run eval; track per-action saving/error (keyed so one slow eval doesn't block edits).
- [x] `src/features/settings/SettingsPage.tsx` — edit each profile's model/temperature/max_tokens
      (provider read-only — only `ollama` is registered); a prompt editor (textarea per file,
      Save writes to disk); a **Run evals** control per suite showing pass/fail counts + failing cases.
- [x] `src/App.tsx` — added a **Settings** nav link. `src/routes/AppRoutes.tsx` — added `/settings`.

### Slice B "done" check
In Settings: change `task_extraction` temperature, Save → re-open confirms it stuck and
`profiles.yaml` on disk is unchanged (override landed in `profiles.local.yaml`). Edit
`extract_tasks.md`, Save, then process a new inbox item → the edited prompt takes effect with no
backend restart. Click **Run evals** → pass/fail counts return. **Commit.**

---

## Cross-cutting / constitution checklist
- [x] No schema change this sprint → **no Alembic migration** (dashboard reads existing tables).
      If any model field gets added, generate and commit a migration.
- [x] No new dependencies (overrides-file approach was chosen specifically to avoid `ruamel.yaml`).
- [x] Every model call goes through `gateway.complete`; the `summary` workflow imports the gateway,
      never Ollama.
- [x] structlog on every new route/service; no bare `print`/`logging`.
- [x] Type hints + return types on every new function (`mypy --strict` clean on the new modules).
- [x] Frontend strict TS, API calls only through `src/api/`, feature-folder layout.
- [x] Update `TASKS.md` (tick the Sprint 5 lines) and `README.md` (mark Sprint 5 done; note the
      `profiles.local.yaml` override mechanism and the `run_summary_evals` dev command) when the sprint
      closes.

---

## Verification (whole sprint)
1. `cd backend && python -m app.main` (Ollama running), `cd frontend && npm run dev`.
2. Dashboard renders instantly; on-demand summaries work and fail gracefully.
3. Settings: profile edit persists to `profiles.local.yaml` (committed `profiles.yaml` untouched),
   prompt edit takes effect without restart, eval run returns counts.
4. `cd backend && python -m pytest` green; `run_evals.py`, `run_match_evals.py`, `run_summary_evals.py`
   pass against Ollama.
