# Current focus

**Epic: Post-deploy hardening & polish** (checked out 2026-07-03).

Goal: act on the legitimate follow-ups surfaced by an external code review now that
the deployable-app epic has shipped (archived in `DONE.md`). Scope is deliberately
narrow — this is a hardening/polish pass, not new features.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Review triage (2026-07-03)

An external (ChatGPT) review flagged five things. Two are **not** acting on:

- **Rate-limiter "most serious bug" — false alarm, no action.** The claim was that
  `rate_limit.py` appends to an orphaned deque after `del _HITS[key]`. It doesn't:
  `_HITS` is a `defaultdict(deque)`, so `del _HITS[key]` drops the empty deque and
  the following `_HITS[key].append(now)` re-indexes the defaultdict — creating a
  fresh deque, storing it, and appending to it. Accumulation is correct.
  `backend/tests/test_rate_limit.py` already proves the N+1th request returns 429.
- **Real auth / internet exposure — deliberate scope decision, stays out.** Trusted
  home LAN is the target; multi-user auth and reverse-proxy-on-the-internet hardening
  remain on the do-not-build list until the deployment target actually changes. See
  "Out of scope" below.

The three legitimate items are the slices below.

---

## Slice 1 — Frontend data-consistency polish (FE-only)

Net-new from the review; small. No backend, schema, or dependency change.

- [x] **`useDashboard` has no reload path** — now exposes `reload()` (refresh-key pattern)
      and subscribes to `taskRefreshVersion` like `useTasks`. Wired via a new
      `onTasksChanged` callback on `InboxCapturePanel` (the dashboard's only mutation
      surface) → `DashboardPage` calls `reload()` after a capture is reviewed/decided.
- [x] **`useTasks` doesn't reset `loading` on refresh** — resolved the "separate flag"
      way: `loading` is now explicitly initial-load-only, and a new `refreshing` flag
      carries in-flight state for reload/`taskRefreshVersion`-driven refetches (avoids a
      full-page spinner flash). Applied identically to `useDashboard`; `DashboardPage`
      exposes it via `aria-busy`.
- [x] Vitest for the new reload / loading behaviour (`useDashboard.test.ts`,
      `useTasks.test.ts`).

## Slice 2 — Task indexes (BE + Alembic migration)

Promoted from TODO's "deferred hardening" — the review independently flagged it as the
next ceiling, and deploy widens the read paths. Add before the dataset grows.

- [ ] Single-column indexes on `tasks`: `deleted_at`, `project_id`, `review_status`,
      `workflow_status`, `parent_task_id`, `recurrence_id` (all table scans today).
- [ ] Compound index(es) for the common active-task query
      (`deleted_at IS NULL` + `review_status`/`workflow_status`) — profile the real list
      queries first, don't add speculative composites.
- [ ] `alembic revision --autogenerate`, review the generated file, apply. Happy-path
      test that the hot queries still return correct rows (indexes are transparent, so this
      is a regression guard, not a perf assertion).

## Slice 3 — Pagination / limits on unbounded list endpoints (BE)

- [ ] `GET /api/tasks` and `GET /api/inbox` are unbounded — give both a sane server-side
      default limit (+ offset or cursor) even though the UI initially requests "all"
      (trash/pending lists are already capped; mirror that shape).
- [ ] Keep the frontend working against the new default (request the cap explicitly if the
      list views genuinely need everything for now).
- [ ] pytest for the limit/offset behaviour.

## Slice 4 (stretch) — Rollup engine subtree scoping

- [ ] **(low)** `_children_map` runs on every task list *and* every single-task read via
      `_read(s)_with_blocked`, loading the whole task table each time. Scope it to the
      requested subtree or memoize per request. Do only if Slices 1–3 land small — this is
      the lowest-value of the deferred notes.

---

## Out of scope (stays in TODO.md / do-not-build)

- Multi-user auth, reverse-proxy-on-the-internet hardening — deployment target is a
  trusted home LAN.
- Command-bar AI chat, Today AI reordering, calendar-aware scheduling.
- Custom model training track (gated on 200+ examples).

## Definition of done for the epic

Every slice meets the CLAUDE.md definition of done (manual vertical path, happy-path
pytest, structured logs, README updates where relevant; Alembic migration for Slice 2).
The epic is done when the three legitimate review items are addressed and the false-alarm
/ out-of-scope decisions above are recorded.
