# Current focus — none committed

> There is **no committed current-focus epic** right now. The planning-view
> (Gantt/calendar) epic was removed (`04dea44` "removed gantt") because it didn't
> earn its complexity — see `DONE.md` ("Planning view (Gantt/calendar) — REMOVED").
> The "Project phases" epic that was queued on top of it has been **de-committed**:
> its Slices 2–3 built phases *into* the per-project Gantt and the global `/planning`
> surface, both of which no longer exist, so it was scoped against a cut surface.
> This file tracks the active epic; until a new one is chosen it names none.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## What's live right now

The **Cleaning & hardening — manual review (round 4)** pass in `TODO.md` (top of
the file) is the active work until a new epic is promoted. No new feature epic is
in flight.

## Candidate next epics (not committed — need a decision before promotion)

- **Project phases, planning-free** — phase grouping on the existing task
  **list/board** (a `phases` table + a nullable `tasks.phase_id`, project-scoped,
  no Gantt). The detailed model/decisions from the old phases epic live in this
  file's git history (pre-`04dea44`-cleanup) and can be revived if promoted.
- **Agent / task orchestration** — the direction `TODO.md` hints at; currently
  undefined and needs scoping before it could become a focus.
