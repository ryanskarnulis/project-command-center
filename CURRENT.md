# Sprint 10 — Settings Page Overhaul

> Goal: bring `/settings` up to par with the Sprint 8–9f Dashboard/Tasks/Inbox/
> Projects/Trash polish. Today [`SettingsPage`](frontend/src/features/settings/SettingsPage.tsx)
> is a bare `<div className="settings">` with one `<h1>` and three stacked
> `<section>`s of plain `<ul>/<li>` editors — no page header, no section nav, no
> cards, no lucide icons, free-text model fields, no dirty-state, no save
> confirmation, and no way to undo a profile override. Everything else now uses
> cards, lucide icons, `.task-filters`, `.page-loading`/`.empty-state`, status
> pills, and nav counts.
>
> Ship as **6 small chunks** (CLAUDE.md: small reviewable diffs, one slice at a
> time), stopping after each for manual review/test/commit. The backend-touching
> pieces (Ollama introspection, reset-override) are isolated into their own chunks
> so the safe frontend parity lands first and each backend diff stays reviewable
> on its own.

> Sprint number assumed **10** (continues the 8–9f polish line; settings is a new
> area). Change the label if you'd rather keep it under 9.

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

## The 9 approved asks → chunk map

(#10, keyboard & a11y polish, was **not** approved and is out of scope.)

| #  | Improvement | Chunk | Touches |
| -- | --- | --- | --- |
| 1  | Section nav / tabs (Profiles · Prompts · Evals) | 1 | FE |
| 2  | Card layout + lucide icons + page header | 1 | FE |
| 3  | Dirty-state tracking + unsaved-changes warning | 2 | FE |
| 4  | Save confirmation ("Saved ✓", auto-clears) | 2 | FE |
| 8  | Prompt editor upgrades (workflow tag, monospace, char count, revert) | 3 | FE |
| 9  | Eval trend (pass-rate over recent runs) + "Run all suites" | 4 | FE |
| 7  | Ollama health panel (connected / host / model + re-check) | 5 | BE + FE |
| 6  | Installed-model dropdown (from Ollama, free-text fallback) | 5 | BE + FE |
| 5  | Reset-to-default for profile overrides | 6 | BE + FE |

## Ground rules (read first)

- **Slice discipline:** Chunks 1–4 are **frontend-only** and reuse the existing
  endpoints (`GET /profiles`, `PATCH /profiles/{name}`, `GET/PUT /prompts`,
  `POST /evals/{suite}/run`, `GET /evals/runs`). Chunk 5 adds two **read-only**
  provider-introspection endpoints; chunk 6 is the only chunk with a settings
  **write** beyond the existing PATCH.
- **Frontend:** React + Vite + TS strict; no `any` without a `// TODO` + reason.
  API calls go through [`src/api/settings.ts`](frontend/src/api/settings.ts);
  components consume [`useSettings`](frontend/src/features/settings/useSettings.ts).
  Plain CSS in [`src/index.css`](frontend/src/index.css) — **reuse existing
  classes** (`.task-card`, the `<h1>`/page-header pattern, `.page-loading`,
  `.empty-state`, `.task-filters`, the `.settings-*` family already present at
  lines ~1298–1451, the pill styles, `.error`); add `.settings-*` only where
  genuinely new.
- **Backend:** Python 3.11+, SQLAlchemy 2.0 typed, Pydantic v2, structlog with the
  request-bound logger. **All model/provider access goes through
  [`app/ai/gateway.py`](backend/app/ai/gateway.py); never `import ollama` outside
  `app/ai/providers/`** (CLAUDE.md prime directive #2). New schemas live in
  [`app/schemas/settings.py`](backend/app/schemas/settings.py); routes in
  [`app/api/routes_settings.py`](backend/app/api/routes_settings.py); logic in
  [`app/services/settings.py`](backend/app/services/settings.py).
- **Write guard:** the existing `require_local_settings_write` dependency limits
  settings *writes* to loopback. Reads (health, model list, eval history) stay
  public like the other GETs.
- Per chunk: `cd frontend && npm run test && npm run build` green; for the
  backend-touching chunks (5, 6) also `cd backend && ./.venv/bin/pytest` green,
  with a new case in
  [`tests/test_routes_settings.py`](backend/tests/test_routes_settings.py).
- One-line commit per chunk at the chunk stop, in the running style:
  `<letters>: Sprint 10 - <chunk> (...)`.

## ⚠️ Flags (raised per CLAUDE.md)

- **LAN write-guard interaction.** The app is reached from LAN devices
  ([[project_lan_networked_setup]]), but `require_local_settings_write` blocks
  settings *writes* from non-loopback clients (403). This already applies to the
  existing profile PATCH and prompt PUT — chunk 6's reset-override inherits the
  same behavior. **No change to the guard** here; just be aware that profile/
  prompt/reset edits only succeed from the host machine, while the new read-only
  health and model-list panels (chunk 5) work from any LAN device. Flag if you
  want the guard relaxed — that's a separate, explicit decision.
- **Extraction model stays `gemma4:e2b`** ([[project_extraction_model_choice]]).
  The chunk-5 model dropdown must **preselect the current value** and only change
  a model when the user explicitly picks one — it must not silently re-default
  `task_extraction` off `gemma4:e2b`.

---

## Chunk 1 — Structural foundation: header, cards, section nav `[ ]`

**Asks:** #1, #2. **Files:** `SettingsPage.tsx`, `index.css`. FE-only.

- Add a real page header matching the other pages: `<h1>Settings</h1>` + a
  one-line description (consistent with the existing `.settings-note` tone).
- Introduce **section navigation**: a sticky sub-nav / tab row (Profiles ·
  Prompts · Evals) that scroll-anchors (or toggles) the three sections, so you
  jump instead of scrolling one long column. Pick scroll-spy *or* tab-switch —
  whichever is simpler with current CSS; note the choice in the commit.
- Convert each `<li>` editor (`ProfileEditor`, `PromptEditor`, eval rows) to the
  shared **card** look and add **lucide icons** per section
  (`SlidersHorizontal` for profiles, `FileText`/`MessageSquare` for prompts,
  `ClipboardCheck` for evals — reuse icons already imported elsewhere).
- Keep all existing behavior/handlers intact; this chunk is purely structure +
  styling. No new endpoints, no logic changes.
- **Done when:** the page reads as a polished, navigable settings screen with
  parity to Projects/Trash; `npm run test && npm run build` green.

## Chunk 2 — Edit safety: dirty-state + save confirmation `[ ]`

**Asks:** #3, #4. **Files:** `SettingsPage.tsx` (`ProfileEditor`, `PromptEditor`),
optionally `useSettings.ts`. FE-only.

- **Dirty-state:** each editor computes whether its inputs differ from the loaded
  value. Disable **Save** when unchanged; show an "unsaved" dot/badge when dirty.
- **Navigate-away warning:** if any editor is dirty, warn before leaving
  (route change + `beforeunload`). Keep it lightweight — a single page-level
  "you have unsaved changes" guard fed by the editors' dirty flags.
- **Save confirmation:** on success, show a transient inline "Saved ✓" that
  auto-clears after a few seconds (extend the existing per-item `ActionState`
  with a `saved` flag rather than adding a toast system).
- **Done when:** Save is gated on real changes, unsaved edits are visible and
  warned-on, and a successful save shows clear confirmation; tests/build green.

## Chunk 3 — Prompt editor upgrades `[ ]`

**Ask:** #8. **Files:** `SettingsPage.tsx` (`PromptEditor`), `index.css`,
possibly `types/settings.ts`. FE-only.

- **Workflow tag:** show which profile(s) consume each prompt, derived on the
  frontend from the already-loaded profiles' `system_prompt` field (e.g.
  `extract_tasks.md → task_extraction`). No backend change.
- **Comfort:** monospace, resizable, taller textarea; live **character count**.
- **Revert-to-last-saved:** a button that restores the editor to the prompt text
  currently loaded in state (pairs with chunk-2 dirty-state).
- **Done when:** prompts are comfortable to edit and clearly tied to their
  workflow; tests/build green.

## Chunk 4 — Eval trend + run-all `[ ]`

**Ask:** #9. **Files:** `SettingsPage.tsx`, `useSettings.ts`, `index.css`. FE-only.

- **Trend:** replace the flat run list with a compact pass-rate trend across the
  recent runs already loaded via `getEvalRuns` (sparkline or pass-rate row per
  run). Keep failing-case details from the latest run.
- **Run all suites:** one button that runs `task_extraction`, `project_matching`,
  and `summary` in sequence (reuse the existing `runEvals`), with per-suite
  progress. No new endpoint.
- **Done when:** eval history reads as a trend and you can trigger all suites at
  once; tests/build green.

## Chunk 5 — Ollama introspection: health panel + model dropdown `[ ]` (BE + FE)

**Asks:** #7, #6. **Files:** `app/ai/providers/` (ollama provider),
`app/ai/gateway.py`, `app/services/settings.py`, `app/schemas/settings.py`,
`app/api/routes_settings.py`, `tests/test_routes_settings.py`,
`src/api/settings.ts`, `src/types/settings.ts`, `useSettings.ts`,
`SettingsPage.tsx`, `index.css`. **Both endpoints are read-only.**

- **Backend (via gateway only):** add provider introspection — a health/ping that
  reports reachable + host, and an installed-models list (Ollama `/api/tags`).
  Expose through `gateway`, then two **GET** routes
  (`/api/settings/ollama/status`, `/api/settings/models`). No `import ollama`
  outside `app/ai/providers/`. Both stay public (reads), no write guard.
- **FE #7 — health panel:** a status row at the top of Settings showing
  connected / host / (loaded model if available) with a **re-check** button.
  Degrade gracefully when Ollama is down (clear "not reachable" state, no crash).
- **FE #6 — model dropdown:** in `ProfileEditor`, replace the free-text model
  input with a dropdown populated from `/api/settings/models`, **preselecting the
  current value** and keeping a free-text fallback (so a not-yet-pulled or custom
  name is still enterable). Respect the extraction-model flag above.
- **Done when:** Settings shows live Ollama status and profiles pick from real
  installed models; backend pytest + a new route test green; FE tests/build green.

## Chunk 6 — Reset-to-default for overrides `[ ]` (BE + FE)

**Ask:** #5. **Files:** `app/services/settings.py`, `app/api/routes_settings.py`,
`app/schemas/settings.py`, `tests/test_routes_settings.py`, `src/api/settings.ts`,
`useSettings.ts`, `SettingsPage.tsx`. **Settings write — loopback-guarded.**

- **Backend:** a service helper that removes a profile's override key(s) from
  `profiles.local.yaml` and reloads, returning the new effective `ProfileRead`
  (so the UI updates `overridden_fields`). Expose as a **DELETE**
  (`/api/settings/profiles/{name}/overrides`, optional `?field=` to clear one
  field; no field = clear all for that profile), guarded by
  `require_local_settings_write`. 404 on unknown profile; no-op safe if no
  override exists.
- **FE:** a **"Reset to default"** control on each profile (enabled only when
  `overridden_fields` is non-empty), wired through `useSettings`; on success the
  inputs reflect the committed `profiles.yaml` value and the "(overridden)" tags
  clear. Reuse chunk-2 save feedback.
- **Done when:** an overridden profile can be reverted to its committed default
  from the host machine; backend pytest + new route test green; FE tests/build
  green.

---

## Suggested order & rationale

1 → 2 → 3 → 4 (safe, frontend-only parity and edit-safety land first and are
independently shippable) → 5 → 6 (backend-touching, each isolated and reviewable
on its own; reset-override last since it's the only new settings write).
