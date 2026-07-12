# TODO

Outstanding work for Project Command Center. No sprint numbers — everything below
is **backlog**, grouped by theme. The single exception is the **current focus**,
which lives in `CURRENT.md`. Completed work is archived in `DONE.md`.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Current focus

**Checked out 2026-07-12 (see `CURRENT.md`):** task due-date reminders,
export tasks to markdown, and the Tasks-page decision — the three Backlog
items below. The Phase 2 agent epics are done and archived in `DONE.md`:
runtime + provider (#36–#38), agent loop / persistence / chat panel / eval
harness (#41–#44), Agent UX polish + ambient entry (#47–#50, follow-ons
#51–#52), and the fleet agent-standard alignment (#54–#56). Personality
shipped with that alignment (#55, layered Glitch); voice (#287) shipped 2026-07-12 via
`../agent-standard/voice.md` (VOICE-PLAN Phase 3, PRs #58–#60).

---

## Phase 2 — Local agent *(shipped 2026-07-10 → 2026-07-11; only the embeddings fallback remains open)*

A full agent for the app: local llama.cpp runtime, tool calling, MCP, retrieval.
The agent is a **peer of the UI, not a bypass** — every write goes through the
same service layer, lands in `activity_events`, and is undoable via soft
delete/trash. Sequence a design/plan slice into `CURRENT.md` before building;
the items below are scope, not order.

### Runtime

- [x] llama.cpp (`llama-server`) integration — shipped 2026-07-10/11 as the
      workspace `../llama-swap/` stack: one proxy owning the RTX 3060, a
      single shared `gemma-4-12b` entry for chess + PCC (proven native tool
      calling), full 128k ctx at 9.5 GB loaded / 10.5 GB peak. Decision +
      measurements in `docs/agent-design.md` ("Runtime").
- [x] Provider layer for chat-completions-with-tools against llama-server —
      shipped 2026-07-11 (#38): `backend/app/ai/providers/llamacpp.py`,
      OpenAI wire format over httpx, `json_schema` structured outputs,
      Pydantic-validated at the boundary with typed errors; live tool-call
      round trip verified via the opt-in integration test
      (`PCC_LLM_INTEGRATION=1`).

### Tool surface (MCP-first)

- [x] **PCC MCP server** exposing the service layer as tools: task CRUD +
      complete, project CRUD, search, focus, trash/restore. Shipped 2026-07-10
      (`backend/app/mcp/`, design in `docs/agent-design.md`); any MCP client
      (Claude Code included) gets full PCC access, and the in-app agent will
      consume the same tools. One tool surface, two consumers.
  - [x] Follow-up: dependencies and recurrence tools (add/remove dependency,
        skip/stop recurrence) — shipped 2026-07-11 (#34), completing the tool
        surface; dependency writes now audited in `activity_events` from
        every caller.
- [x] Tool-level guardrails: no hard deletes, argument validation, per-tool
      audit entries in `activity_events` attributed to the agent
      (`actor = "agent:mcp"`; shipped with the server, 2026-07-10).

### Agent loop

- [x] Backend agent loop (tool-runner over the provider layer) — shipped
      2026-07-11 (#41): `app/ai/loop.py` over the transport-agnostic
      registry (`app/tools/`, schemas byte-identical to the MCP
      `inputSchema`s); bounded iterations + bounded self-correction on
      schema-invalid calls; every mutation audited as `agent:loop`.
- [x] Conversation/session persistence — shipped 2026-07-11 (#42):
      `conversations` + `conversation_messages` (migration `7efad5645027`),
      tool trajectory stored on the assistant turn, `/api/agent` REST
      surface rate-limited via `agent_messages_per_min`.

### Retrieval / RAG

- [x] Start with **agentic retrieval over FTS5** — shipped with the loop:
      the `search` + `list_activity` tools are the retrieval story; the eval
      baseline (#44) found the described task via FTS5 on every run.
- [ ] Embeddings only if FTS proves insufficient: `sqlite-vec` inside the
      existing SQLite DB (no external vector store), embedding model served
      locally.

### UI

- [x] Chat panel feature (`features/agent/`) — shipped 2026-07-11 (#43):
      visible tool trajectory with per-mutation undo, conversation sidebar;
      non-streaming v1 by decision (SSE only if usage demands); markdown
      replies + trajectory entity links landed in the UX epic (#48).
- [x] Agent becomes the capture surface that inbox used to be — shipped
      2026-07-11 (#50): ambient agent entry from the search bar (inline
      exchange, "Continue in Agent" opens `/agent/:id`), undo as the safety
      net; slash commands removed wholesale.

### Quality

- [x] Agent eval harness — shipped 2026-07-11 (#44):
      `tests/test_agent_evals.py` (opt-in `PCC_AGENT_EVALS=1`), six scenarios
      asserting trajectory shape + DB end-state + audit invariants against
      the real model; 24/24 baseline over 4 runs recorded in
      `docs/agent-design.md`.

---

## Backlog

*(Non-agent feature work — all three items checked out 2026-07-12, see
`CURRENT.md`.)*

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
