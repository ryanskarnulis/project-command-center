# Current Sprint

## Sprint 9h — Training Data Page UX Overhaul

The Training Data page (`frontend/src/features/training/`) is the last page that
predates the Sprint 9 UI overhaul. This sprint brings it up to par with
Settings/Projects/Trash and sharpens the corpus-inspection workflow.

**Scope guard:** this is UI polish, *not* Sprint 10. Sprint 10 (export
`ai_training_examples` → Unsloth fine-tune → llama.cpp swap) stays untouched.
The "fine-tune readiness panel" below is presentation only — it does **not**
build export. No schema changes, no Alembic migrations, no AI workflow changes,
so no new eval cases are required. One backend search filter is added (read-only,
no migration) and gets a happy-path test.

All 10 improvements approved by the user.

### Files in play

- `frontend/src/features/training/TrainingPage.tsx` — the page (rebuilt in chunks)
- `frontend/src/features/training/useTraining.ts` — data hook (filters, pagination)
- `frontend/src/api/training.ts` — API layer (add `search`, `offset` wiring)
- `frontend/src/types/training.ts` — `TrainingFilters` gets `search`
- `frontend/src/index.css` — training-* styles (extend existing block ~L1297+)
- `backend/app/api/routes_training.py` — add `search` query param
- `backend/app/services/training_data.py` — add ILIKE filter to `list_examples`
- `backend/tests/test_routes_training.py` — search-filter happy-path test

### Chunk plan (each chunk = one reviewable, commit-sized diff)

**Chunk A — Page shell consistency (#1, #3) — ✅ DONE**
- Replace bare `<h1>` with the shared `section-heading` pattern + a lucide icon
  (`Database` or `GraduationCap`), matching Settings/Projects/Trash.
- Replace plain `<p>Loading…</p>` / "No examples" with the shared `page-loading`
  and `empty-state` styling (icon + message).
- Add/align `training-*` CSS to the current design tokens.

**Chunk B — Stats panel + by-task + readiness (#9, #10) — ✅ DONE**
- Keep the goal meter driven by the unfiltered corpus total (the 200-row goal is
  a corpus property, not a filtered one).
- Add an accepted-rate readout and a "showing N (filtered)" count derived from
  the loaded list, so filtered views read honestly without a new stats endpoint.
- Replace the by-task bullet list with count chips (task + count, accepted share).
- Sharpen the progress card into a "fine-tune readiness" panel framed for Sprint
  10 export (UI copy only — no export action).

**Chunk C — Filter panel + search (#2) — ✅ DONE**
- Backend: add `search: str | None` query param to `GET /training-examples`;
  `list_examples` applies a case-insensitive ILIKE over `input_text` and
  `model_output_json`. Read-only, no migration. Structured log + type hints.
- Backend test: happy-path search filter in `test_routes_training.py`.
- Frontend: `TrainingFilters.search`, wire through `api/training.ts`. Debounced
  (300ms) into the backend-side filter so it stays correct under pagination.
- Replace the two raw `<select>`s with the shared `task-filters` panel (header,
  `task-search-field`, filter grid for task + status, Clear button).
- Folded in the Chunk B remnant: per-chip accepted share (`TaskStat` widens the
  `by_task` stats shape — Pydantic-only, no migration).

**Chunk D — Pagination / load more (#4) — ✅ DONE**
- `useTraining` gains `offset` + append semantics and a `hasMore` flag (derived
  from whether the last page returned a full `limit`).
- "Load more" button at the list foot; resets on filter change. Backend already
  supports `offset` — no backend change.

**Chunk E — Per-example header: status pills, metadata, copy (#6, #7, #8) — ✅ DONE**
- Richer status taxonomy: distinguish **accepted** / **corrected**
  (`corrected_output_json !== null`) / **extraction-failure**
  (`corrected === null && !accepted`) with color-coded pills.
- Show `created_at` (relative, via existing `utils/dates.formatRelative`) and
  `model_profile` in the example header alongside `model_name`.
- Copy-to-clipboard button on each JSON block (input / output / corrected).

**Chunk F — Correction diff view (#5) — ✅ DONE**
- Inline diff of `model_output_json` vs `corrected_output_json` (both pretty-
  printed first), shown when a correction exists. Hand-rolled line-level diff —
  **no new dependency** (CLAUDE.md: prefer stdlib / ask before deps).
- Default the diff `<details>` open for corrected examples; keep raw blocks
  available for the full text.

### Build order & notes

A → B → C → D → E → F. A establishes the shell; B/C/D touch the hook + filters
together; E/F are per-example content (the corpus-inspection payoff).

- Search is backend-side (not client-side over the loaded page) so it stays
  correct under pagination.
- No state library, no CSS framework (CLAUDE.md frontend rules). Plain CSS in
  `index.css`, React state in the feature hook.
- Done per chunk = renders correctly in the app; backend chunk (C) also has its
  happy-path test passing.

---

- Completed work lives in `DONE.md`.
- Incomplete items / follow-ups live in `TODO.md` (see "Sprint 9g — Remaining").
- The sprint roadmap is in `README.md` (next up after 9h: Sprint 10 — export
  `ai_training_examples` → Unsloth fine-tune → llama.cpp swap, gated on 200+
  training examples).
