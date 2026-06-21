# Current Sprint

## Sprint 9m — Command-bar slash actions (`/new`, `/done`)

**Status:** `[x]` done — shipped and archived to DONE.md (README Sprint 9m). The
recommended decision was taken: `SearchResultItem` carries `review_status`/
`workflow_status` serialized off existing columns (no migration). The plan below is
kept as the build record.

Extend the already-generic `CommandSearch` topbar so a leading `/` switches the bar
from search into an action. Two actions this slice: `/new <text>` (capture messy
text into the inbox and run extraction) and `/done <task>` (fuzzy-find a task and
mark it done). The bar, debounce, keyboard nav, and grouped dropdown already exist
(Sprint 9j); `CommandSearch` was deliberately built generic for exactly this
(`KIND_META` routing lives in data, nav runs over a `flat` list — see
`frontend/src/features/search/CommandSearch.tsx:11`). This is mostly frontend and
reuses existing backend routes. `/new` feeds the capture → review → training-data
loop, which is the north star.

### Why this slice

- The infrastructure was explicitly stubbed for it; this finishes a deliberate seam
  rather than opening a new one.
- `/new` is the fastest path from "thought" to inbox candidate → correction →
  `ai_training_examples`, the corpus everything else is gated on.
- No schema change, no migration, no model/profile/eval change, no new dependency.

### Scope (what ships)

1. **Command parsing layer.** A small pure helper that turns the raw input into a
   discriminated command:
   - `/new <text>` → `{ kind: 'new', text }`
   - `/done <query>` → `{ kind: 'done', query }`
   - anything else (no leading `/`, or unrecognized verb) → `{ kind: 'search', query }`
   - bare `/`, `/new`, `/done` with empty argument → a hint/disabled state, not an
     action.
   Keep it case-insensitive on the verb; trim the argument. Put it next to the
   feature (`frontend/src/features/search/parseCommand.ts`) so it's unit-testable
   without rendering.

2. **`/new <text>` — capture + extract.**
   - Dropdown shows a single confirm action: "Capture & extract: <text>".
   - On select/Enter: `createInbox({ raw_text })` → `processInbox(id)` →
     `navigate('/inbox/:id')` to land on the existing note-review route.
   - Reuses `frontend/src/api/inbox.ts` (`createInbox`, `processInbox`) untouched.
   - Toast on success/failure via the existing `useToast`. Disable the action while
     in flight so a double-Enter can't create two inbox items.
   - Idempotency is already handled server-side (input-hash dedupe), so a repeat
     `/new` of the same text returns the existing item — no client guard needed
     beyond the in-flight lock.

3. **`/done <query>` — resolve + complete.**
   - Reuse `GET /api/search?q=<query>` (via the existing `useSearch` debounce) and
     show **only the tasks group** as a disambiguation list.
   - Selecting a match calls `markTaskDone(id)` (`POST /api/tasks/{id}/done`) — the
     dedicated done endpoint, **not** `PATCH`, so recurrence (next-occurrence
     creation) is preserved. (TODO.md's note suggesting PATCH is stale.)
   - Toast naming the task that was completed; refresh is implicit (the user is on
     the command bar, not a list). If zero matches, show the standard empty state.

4. **Unified action model in `CommandSearch`.** Generalize the current
   `flat`/`KIND_META`-driven render so each dropdown row is an action with a label,
   optional badge, and an `onSelect()` — search results, the `/new` confirm row, and
   `/done` task rows all become rows in one list. Keyboard nav (Arrow/Enter/Escape)
   already iterates `flat`; it should keep working unchanged once rows carry their
   own `onSelect`.

5. **Discoverability.** Update the input placeholder and add a one-line hint row in
   the dropdown when the query is just `/` (e.g. "`/new` capture · `/done` complete a
   task"). No separate help system.

### The one real decision: `/done` matching a non-open task

`SearchResultItem` carries no `review_status`/`workflow_status`, so search will
happily return candidates and already-done tasks. Marking a *candidate* "done" is
semantically wrong, and offering an already-done task is noise.

**Recommended:** add `workflow_status` and `review_status` to `SearchResultItem`
(`backend/app/schemas/search.py` + populate in `services/search.py`; both already on
the `Task` row, so it's two extra fields, no migration, no new query). The command
bar then filters `/done` candidates to `review_status == "accepted" &&
workflow_status != "done"`. Plain search ignores the new fields, so nothing else
changes. This is the smallest correct option.

**Fallback if we want zero backend change:** resolve only via the existing fields
and call `markTaskDone`, accepting that it can target a candidate/done task. Cheaper
but lets the bar do a semantically wrong thing — not recommended.

> Assumed we take the recommended option (two serialized fields, no migration).
> Flag if you'd rather keep the search schema frozen.

### Files

Backend (only if recommended decision is taken):
- `backend/app/schemas/search.py` — add `workflow_status`, `review_status` to
  `SearchResultItem` (optional/nullable; projects & inbox leave them `None`).
- `backend/app/services/search.py` — populate the two fields for the tasks group.

Frontend:
- `frontend/src/features/search/parseCommand.ts` — new pure parser.
- `frontend/src/features/search/CommandSearch.tsx` — command mode, unified action
  rows, `/new` + `/done` handling, placeholder/hint.
- `frontend/src/types/search.ts` — mirror the two new optional fields (if added).
- `frontend/src/api/*` — no new functions; reuse `inbox.ts` + `tasks.ts` + `search.ts`.

### Testing

- Backend: extend the search service test to assert tasks carry
  `workflow_status`/`review_status` and projects/inbox serialize them as `null`
  (only if the schema change lands).
- Frontend: `parseCommand` unit tests (search vs `/new` vs `/done` vs empty-arg vs
  unknown verb, case-insensitivity). Extend `CommandSearch.test.tsx`: `/new` row
  appears and triggers capture+navigate (mock `createInbox`/`processInbox`); `/done`
  lists task matches and calls `markTaskDone` on select; non-slash input still
  searches as before.
- `pytest` green; `npm run test` green (watch the known-flaky
  `TaskDetailPage.test.tsx` — pre-existing, unrelated).

### Done criteria (per CLAUDE.md)

1. `/new` works UI → API → DB → inbox-review UI; `/done` works UI → API → DB.
2. At least one happy-path test on each new path (above).
3. No raw `print`/unstructured logging introduced; reused routes already log with
   request IDs.
4. No AI surface added (search stays deterministic; `/new` reuses the existing
   extraction workflow + its eval/validation — nothing new to eval).
5. No schema change → **no Alembic migration** (the two new fields are serialized
   off existing columns, not DDL).
6. README sprint status + Settings/command-bar docs updated; TODO.md item checked
   off; this slice archived to DONE.md.

### Out of scope (explicitly not this slice)

- AI chat in the command bar (the third future use of the generic input) — later.
- `/done` Discord command and `GET /api/discord/tasks/search` — separate Discord
  backlog item.
- Any new slash verb beyond `/new` and `/done`.
- Global `Cmd-K` focus wiring if not already present (the `<kbd>Cmd K</kbd>` hint
  exists; only wire the shortcut if it's trivial — otherwise leave for a polish pass).

---

## Recently Completed

- Sprint 9m — Command-bar slash actions: `/new <text>` (capture → extract →
  note-review) and `/done <task>` (fuzzy-find → complete via the recurrence-preserving
  done endpoint) on the generic `CommandSearch` bar, via a pure `parseCommand` parser
  and unified action rows. `SearchResultItem` gained `review_status`/`workflow_status`
  (no migration) so `/done` offers only accepted, not-done tasks.
- Sprint 9L — Recurring task stubs: optional `repeat_interval`, `recurrence_id`
  series chaining, automatic next-occurrence creation, skip-this-occurrence,
  future-series edits, task-detail/card UI.
- Sprint 9k — Today / daily schedule: deterministic `/today` route and UI backed by
  a pure Python scheduler.
- Sprint 9j — UX foundation + global search (the `CommandSearch` bar this slice
  extends).
