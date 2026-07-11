# TODO

Outstanding work for Project Command Center. No sprint numbers — everything below
is **backlog**, grouped by theme. The single exception is the **current focus**,
which lives in `CURRENT.md`. Completed work is archived in `DONE.md`.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Current focus

**Phase 2 kickoff — tasks-table cleanup, agent design, PCC MCP server**
(checked out 2026-07-10, tracked in `CURRENT.md`). The dashboard-redo epic is
done (archived in `DONE.md`). This kickoff drops the dead AI-era task columns,
writes the agent design doc, and builds the PCC MCP server; the llama.cpp
runtime is deliberately deferred to a later checkout (GPU contention — see
`../future-plans/llama-swap.md`).

---

## Phase 2 — Local agent *(the north star; kickoff checked out 2026-07-10)*

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

- [ ] **Decide the fate of the Tasks page.** The sidebar is gone (2026-07-10);
      Focus/Tasks/Trash live in the topbar. Usage so far is board + Focus only.
      If the cross-project filter/list view stays unused, delete `TasksPage`
      and its filter machinery wholesale (rule 4 of definition-of-done) — but
      keep `/tasks/:id` detail routes alive for search and deep links, as
      project detail survived the Projects-page removal.
      *Decision 2026-07-10: keep as-is for now; re-evaluate once the Phase 2
      agent surfaces settle real usage.*
- [ ] Task due-date reminders
- [ ] Export tasks to markdown

*(The tasks-table post-strip cleanup — dropping `review_status`, `confidence`,
`assignee_hint` — moved into the Phase 2 kickoff epic in `CURRENT.md`,
2026-07-10; `assignee_hint` is dropped, not promoted.)*
