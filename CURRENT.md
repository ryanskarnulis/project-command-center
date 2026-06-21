# Sprint 9o — Command bar completion: `Cmd/Ctrl+K` shortcut + search relevance

## Why this sprint

The command bar is the most-touched surface of the last few sprints (9j global
search, 9m slash actions, 9n Today actionability), but two advertised/expected
behaviours are still stubbed:

1. The bar renders a `Cmd K` hint (`CommandSearch.tsx:245`) but **never listens
   for the shortcut** — there is no global key handler, so the affordance lies.
2. Global search ranks every group **newest-first** (`ORDER BY id DESC` in
   `services/search.py`), so an exact title match loses to an unrelated newer row,
   and candidate/done noise sorts alongside live work.

Both are in the **Command Bar / Search** backlog group, both are tightly scoped,
and they finish the feature instead of starting a new one. This is the strongest
coherent themed slice available.

**Deliberately deferred** (kept out to keep the diff reviewable):
- *Command-bar AI chat* — pulls in a new `ai/gateway.py` workflow + eval cases +
  training capture. That is its own sprint; the `parseCommand`/ActionRow seam it
  hangs off is already in place and unaffected by this work.
- *Task filter URL sync* and *Shell truthfulness pass* — different themes.

## Scope check against CLAUDE.md / README

- No new dependency (uses React + stdlib SQLAlchemy only). ✅
- No schema change → **no Alembic migration**. ✅
- **No model call** → no eval-case requirement triggered. ✅ (search stays pure
  SQL/Python — the backlog item explicitly requires this.)
- Not on the "Do not build yet" list. ✅
- Diff stays small and reviewable (one backend service, schema-free; one frontend
  component + tests). ✅

---

## Slice 1 — Global `Cmd/Ctrl+K` focus shortcut (frontend-only)

**Goal:** `Cmd+K` (mac) / `Ctrl+K` (win/linux) from anywhere focuses the command
bar input, opens the dropdown, and selects existing text; `Escape` returns focus
cleanly (already half-handled in `onKeyDown`).

### Changes — `frontend/src/features/search/CommandSearch.tsx`
- Add an `inputRef = useRef<HTMLInputElement>(null)` and attach it to the `<input>`.
- Add a `useEffect` that registers a `window` `keydown` listener:
  - Match `(e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k'`.
  - `e.preventDefault()` (browsers bind Ctrl+K to the URL bar / search), then
    `inputRef.current?.focus()`, `inputRef.current?.select()`, and `setOpen(true)`.
  - Clean up the listener on unmount.
- Restore focus on `Escape`: the input already blurs on Escape (`onKeyDown`); leave
  that as-is. The shortcut just re-focuses — no global "was focused before" tracking
  needed (out of scope, avoid over-engineering).

### Tests — `frontend/src/features/search/CommandSearch.test.tsx`
- Add a case: fire a `keydown` with `{ key: 'k', metaKey: true }` on `window` and
  assert the input has focus and the dropdown opening path is exercised (e.g. typing
  then the listbox appears). Add a `ctrlKey: true` variant to cover non-mac.
- Assert the shortcut does **not** fire on a bare `k` keypress.

### Acceptance
- From any route, `Cmd/Ctrl+K` focuses + selects the bar; the browser's default is
  suppressed. `Escape` blurs. Vitest green.

---

## Slice 2 — Search relevance ranking (backend, pure SQL/Python)

**Goal:** within each kind, rank exact/prefix title matches above substring matches
above description/raw-text-only matches; for tasks, prefer accepted-and-not-done
over candidate/done noise. Keep it deterministic, no model call.

### Changes — `backend/app/services/search.py`
Replace the per-kind `ORDER BY <pk> DESC` with a computed relevance score, ordered
`score ASC (best first), id DESC (recency tiebreak)`. Implement the score as a
SQLAlchemy `case(...)` expression so ordering happens in SQL (no Python re-sort,
no fetch-then-sort). Reuse the existing `_escape_like` helper for the literal
prefix/exact patterns.

Suggested rank tiers (lower = better), applied per kind via `case()`:

- **Projects** (over `name` / `description`):
  - 0 — `name` equals query (case-insensitive)
  - 1 — `name` starts with query
  - 2 — `name` contains query
  - 3 — matched on `description` only
- **Tasks** (over `title` / `description`), combine a *text* tier with a *state*
  bias so live work wins ties:
  - text tier: same 0–3 ladder over `title` then `description`
  - state bias: order accepted-not-done before done/candidate tasks only after
    the text tier has already been chosen, so an exact title hit still beats an
    unrelated description-only match.
  - Encode as separate sort keys: `text_tier ASC`, `state_score ASC`, then
    `id DESC`.
- **Inbox** (over `raw_text` / `summary`):
  - 0 — `summary` contains query (the human-facing line)
  - 1 — `raw_text` only

Keep the three exact/prefix patterns as separate escaped `LIKE` params
(`q`, `q%`, `%q%`) — all wildcard-escaped via `_escape_like`, matching the existing
`escape=_LIKE_ESCAPE` usage. Per-kind `limit(per_kind)` stays; only the `order_by`
changes (plus the score expression). No new columns selected, no N+1 (project-name
resolution loop is untouched).

> Note: SQLite `LIKE` is case-insensitive for ASCII by default, consistent with the
> existing `ilike`. Keep using `ilike` for parity; equality/prefix tiers use
> `func.lower(col) == func.lower(:q)` / `ilike('q%', escape=...)`.

### Schema / API
- `SearchResults` payload shape is identical; only ordering within each list differs.
  `schemas/search.py` uses the existing task status enums for type alignment. The
  frontend needs no change for slice 2 (it renders groups in received order).

### Tests — `backend/tests/test_search.py`
- Add cases asserting order, not just membership:
  - An exact-title task sorts before a newer task that only matches on description.
  - A prefix match sorts before a mid-string substring match.
  - An accepted+open task sorts before a done/candidate task at the same text tier.
  - Inbox: a `summary` match sorts before a `raw_text`-only match.
- Keep/confirm the existing wildcard-escape and per-kind-cap tests still pass.

### Acceptance
- `GET /api/search?q=...` returns the same grouped shape with relevance ordering.
  `pytest backend/tests/test_search.py` green; full `pytest` green.

---

## Out of scope / non-goals
- No cross-kind global ranking (projects vs tasks vs inbox stay separate groups).
- No fuzzy/trigram/FTS5 — deterministic `LIKE` + `case()` only, per the backlog
  constraint ("keep the implementation pure SQL/Python, no model call").
- No change to `/new` / `/done` slash behaviour or `parseCommand`.

## Definition of done (per CLAUDE.md "done" checklist)
1. Vertical path works manually: `Cmd/Ctrl+K` focuses the bar; searching a known
   exact title surfaces it first.
2. Backend happy-path + ordering tests (pytest); frontend shortcut test (Vitest).
3. Structured logging unchanged (search route already binds request id; no new
   log lines needed).
4. No AI surface touched → no eval/Pydantic-validation obligation.
5. No schema change → no migration.
6. `README.md` sprint plan: add a **Sprint 9o** entry; mark the two backlog items
   done in `TODO.md` (Command-bar focus shortcut, Search relevance pass) with a
   one-line pointer like the other shipped rows.

## Suggested commit chunking
- Chunk A: Slice 1 (Cmd+K shortcut + Vitest test).
- Chunk B: Slice 2 (search relevance service + pytest ordering tests).
- Chunk C: README Sprint 9o entry + TODO.md status flips.
