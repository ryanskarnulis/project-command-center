# TODO

Outstanding work for Project Command Center. No sprint numbers — everything below
is **backlog**, grouped by theme. The single exception is the **current focus**,
which lives in `CURRENT.md`. Completed work is archived in `DONE.md`.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Current focus

**Dashboard redo — board-first UI** (checked out 2026-07-09, tracked in
`CURRENT.md`). The dashboard becomes a project-swimlane board, Today is renamed
to Focus, project task views default to kanban, and the retired AI-era project
aliases are removed before Phase 2 begins.

---

## Phase 2 — Local agent *(the new north star; starts after the dashboard epic)*

A full agent for the app: local llama.cpp runtime, tool calling, MCP, retrieval.
The agent is a **peer of the UI, not a bypass** — every write goes through the
same service layer, lands in `activity_events`, and is undoable via soft
delete/trash. Sequence a design/plan slice into `CURRENT.md` before building;
the items below are scope, not order.

### Runtime

- [ ] llama.cpp (`llama-server`) integration: model choice needs solid native
      tool-calling support; document VRAM footprint on the shared RTX 3060
      (coordination with the chess app's server — see
      `../future-plans/llama-swap.md`).
- [ ] Provider layer for chat-completions-with-tools against llama-server
      (structured outputs where useful; the old Pydantic-validate-everything
      discipline still applies at the boundary).

### Tool surface (MCP-first)

- [ ] **PCC MCP server** exposing the service layer as tools: task CRUD +
      complete, project CRUD, search, Today, trash/restore, dependencies,
      recurrence. This is the highest-leverage piece — any MCP client (Claude
      Code included) gets full PCC access, and the in-app agent consumes the
      same tools. One tool surface, two consumers.
- [ ] Tool-level guardrails: no hard deletes, argument validation, per-tool
      audit entries in `activity_events` attributed to the agent.

### Agent loop

- [ ] Backend agent loop (tool-runner over the provider layer): plan → call
      tools → observe → respond; bounded iterations; every mutation logged.
- [ ] Conversation/session persistence (new table(s) + migration).

### Retrieval / RAG

- [ ] Start with **agentic retrieval over FTS5**: give the agent search tools
      over tasks/projects/activity history and let it query. Local-first, no new
      infra.
- [ ] Embeddings only if FTS proves insufficient: `sqlite-vec` inside the
      existing SQLite DB (no external vector store), embedding model served
      locally.

### UI

- [ ] Chat panel feature (`features/agent/`): streaming responses, visible tool
      calls ("agent created task X — undo"), session history.
- [ ] Agent becomes the capture surface that inbox used to be (paste messy
      text → agent proposes and creates tasks, with undo as the safety net
      instead of a review queue).

### Quality

- [ ] Agent eval harness: scripted scenarios asserting tool-call trajectories
      and end-state DB assertions (spiritual successor to the old eval suites).

---

## Backlog

*(Non-agent feature work — unprioritized.)*

- [ ] Task due-date reminders
- [ ] Export tasks to markdown
- [ ] **Tasks-table post-strip cleanup** (deferred from the merged Slice 1+2 —
      see `CURRENT.md`). With AI extraction and the inbox gone, three `tasks`
      columns are now dead weight but were left in place to keep the strip PR
      bounded:
      - `review_status`: nothing produces `candidate` anymore — collapse the
        column, the pervasive `review_status == accepted` service filtering, and
        the `(deleted_at, review_status)` compound index.
      - `confidence`: AI-extraction output, always `None` now, still surfaced in
        `TaskRead` — drop it.
      - `assignee_hint`: AI-extraction hint, still settable via the task API —
        decide whether it becomes a real "assignee" field or is dropped.
