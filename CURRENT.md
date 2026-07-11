# Current focus

**Epic: Phase 2 kickoff — tasks-table cleanup, agent design, PCC MCP server**
(checked out 2026-07-10).

The dashboard-redo epic is done and archived in `DONE.md`. Phase 2 (the local
agent) starts here, sequenced so the highest-leverage, lowest-risk pieces land
first: clean the dead AI-era columns out of the service layer the agent's
tools will wrap, write the agent design doc, then build the PCC MCP server.
The llama.cpp runtime comes in a later checkout.

Decisions already made (don't relitigate):

- **MCP server before llama.cpp runtime.** The MCP server needs no GPU and no
  provider layer — it wraps the existing service layer, and Claude Code
  becomes PCC's first agent client the day it merges. The runtime slice is
  entangled with the GPU-contention story (`../future-plans/llama-swap.md` —
  phase-0 triggers already observed); deferring it doesn't block agent
  progress.
- **`assignee_hint` is dropped, not promoted** (decided 2026-07-10).
  Single-user app; nothing sets it deliberately anymore.
- **`review_status` and `confidence` are dropped.** Nothing produces
  `candidate` or a confidence since the strip; the pervasive
  `review_status == accepted` service filtering goes with them.
- **The Tasks page stays as-is for now** (decided 2026-07-10). Its fate
  remains a backlog item in `TODO.md`; nothing in this epic touches it.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Slices (one PR each, squash-merged on green CI)

### Slice 1 — Tasks-table cleanup (drop the dead AI-era columns)

- [ ] Alembic migration dropping `review_status`, `confidence`, and
      `assignee_hint`, plus the `(deleted_at, review_status)` compound index —
      replace it with a plain `deleted_at` index so the trash scan keeps its
      coverage. Review autogen; verify upgrade/downgrade round-trip.
- [ ] Backend: remove the fields from `db/models.py`, `schemas/tasks.py`,
      `schemas/search.py`, and `routes_tasks.py`; strip the `review_status`
      filtering from services (`tasks`, `search`, `focus`, `dashboard`,
      `task_dependencies`, `task_recurrence`); tests follow.
- [ ] Frontend: drop the fields from `types/task.ts` / `types/search.ts` and
      their display/edit surfaces (`TaskCard`, `TaskDetailView`,
      `TaskFormModal`, `QuickAddBar`, `CommandSearch` + related CSS); tests
      follow.
- [ ] Doc pass: `README.md` schema/API mentions.

### Slice 2 — Agent design doc

- [ ] Short in-repo design doc covering: the MCP tool surface (task CRUD +
      complete, project CRUD, search, focus, trash/restore, dependencies,
      recurrence); guardrails (no hard deletes, argument validation at the
      boundary, per-tool `activity_events` attribution); server transport and
      how Claude Code connects; the MCP server dependency to add (needs
      sign-off per `CLAUDE.md` before slice 3); and what's explicitly deferred
      (runtime, chat UI, RAG).

### Slice 3 — PCC MCP server (first pass)

> Checklist refined by slice 2's design doc; the scope below is the working
> assumption.

- [ ] MCP server exposing the service layer as tools: task CRUD + complete,
      project CRUD, search, focus, trash/restore.
- [ ] Tool-level guardrails: no hard deletes, argument validation, audit
      entries in `activity_events` attributed to the agent.
- [ ] Verified end-to-end from Claude Code: create → list → complete → trash →
      restore a task through the tools, each action visible in
      `activity_events`.
- [ ] Doc pass: `README.md` setup/usage for connecting an MCP client.

---

## Out of scope for this epic

- llama.cpp runtime, provider layer, agent loop, chat panel UI, RAG — later
  Phase 2 checkouts (`TODO.md`).
- The Tasks-page decision, due-date reminders, markdown export — backlog.

## Definition of done for the epic

All three slices merged; `./test.sh` and CI green; the three columns are gone
from schema and code with a reviewed migration; the design doc is merged; and
a task can be created, completed, trashed, and restored from Claude Code via
the MCP server with every action recorded in `activity_events`.
