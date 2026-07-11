# Current focus

**Epic: Phase 2 — agent loop, conversation persistence, chat panel, eval
harness** (checked out 2026-07-11).

Everything the agent needs to exist has shipped (archived in `DONE.md`): the
MCP tool surface (complete, guardrailed, audited), the shared gemma-4-12b
runtime (`../llama-swap/`, port 8200), and the provider layer
(`app/ai/providers/llamacpp.py` — tool calling + structured outputs,
validated at the boundary). This epic assembles them into the actual agent: a
backend loop that plans → calls tools → observes → responds, persisted
conversations, a chat panel to drive it, and an eval harness that keeps
tool-calling honest on the local model.

Decisions already made (don't relitigate):

- **The loop is another consumer of `services/`, via the tool layer.** It
  calls the same tool functions the MCP server exposes, in-process — no stdio
  hop; the tool layer was scoped transport-agnostic for exactly this
  (`docs/agent-design.md`). No hard deletes, Pydantic at every boundary,
  every mutation in `activity_events` with the loop's own actor value
  (design doc: the in-app loop stamps its own; pick the string in slice 1
  and record it here).
- **Self-correction lives in the loop, not the provider** (established in
  the provider slice): on `ToolCallArgumentsError` / argument-validation
  failure, feed the error back for a bounded number of correction turns,
  chess-style. Iterations are bounded overall — the loop always terminates.
- **Retrieval is the `search` tool** (agentic FTS5). No new infra, no
  embeddings unless the eval harness proves FTS insufficient.
- **Rate limiting applies to the agent endpoints** — `api/rate_limit.py` was
  kept alive for exactly this (constitution, network rules).

Open decisions (resolve in the slices, record the outcome here):

- **Persistence shape.** Conversations/messages as new tables (Alembic
  migration); how tool calls/results are stored on a message vs. recomputed
  from `activity_events`; soft-delete semantics for conversations.
- **Streaming.** The provider is request/response today; the chat panel
  wants progressive output. Either add SSE streaming to the provider
  (llama-server supports it) or ship v1 non-streaming with visible per-step
  tool-call progress and stream later. Decide when the panel UX is concrete —
  don't pre-build streaming the loop can't use yet.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Slices (one PR each, squash-merged on green CI)

### Slice 1 — Tool registry + agent loop core

- [ ] Factor the MCP server's tool bodies into a transport-agnostic registry
      both the MCP server and the loop consume (names, descriptions,
      argument schemas, dispatch) — MCP behavior identical, its tests stay
      green; the registry emits the provider's `ToolSpec`s.
- [ ] The loop (`app/ai/` or `app/agent/` — pick and record): system prompt,
      bounded iterations, dispatch through the registry with Pydantic
      argument validation, bounded self-correction turns on invalid calls,
      terminate on a text turn. Mutations stamped with the loop's actor
      value; structlog carries request ID + the provider's `llm_call_id`.
- [ ] Tests: scripted fake provider (no GPU) driving a create/complete flow
      end-to-end — asserts tool dispatch, DB end-state, audit rows, and the
      self-correction path.

### Slice 2 — Conversation persistence + agent API

- [ ] Schema + Alembic migration: conversations and messages (tool
      calls/results included per the persistence-shape decision).
- [ ] Service module (the only write path, as ever) + REST endpoints:
      create/list conversations, post a user message (runs the loop), fetch
      history. Rate-limited; request-ID logs.
- [ ] Happy-path pytest for service + routes.

### Slice 3 — Chat panel UI (`features/agent/`)

- [ ] Panel: conversation list/history, composer, visible tool calls with
      undo affordance ("agent created task X — undo" → trash restore),
      sensible in-progress states. API layer in `src/api/`, hooks per
      frontend rules; no state library.
- [ ] Verify with the `verifier-browser` skill (rendered-surface change),
      not just Vitest.

### Slice 4 — Eval harness

- [ ] Scripted scenarios asserting tool-call trajectories and end-state DB
      assertions against the real model — opt-in like the provider's live
      test (GPU); documented run command.
- [ ] Baseline results on gemma-4-12b recorded in `docs/agent-design.md`
      (this is the tripwire that would ever justify revisiting the model
      choice or adding embeddings).

---

## Out of scope for this epic

- Agent as the capture surface (inbox successor) — needs the panel to exist
  first; next checkout candidate.
- RAG beyond the `search` tool; `sqlite-vec`; embedding models.
- Tasks-page decision, due-date reminders, markdown export — backlog.
- llama-swap phase 3 (retiring host Ollama) — separate chore once the quiet
  week on `journalctl -u ollama` completes (counted from 2026-07-10).

## Definition of done for the epic

From the chat panel: ask the agent to create/complete/reschedule tasks →
correct tool calls through the loop → every mutation audited with the agent's
actor value and undoable from the trash; conversations survive a reload; the
eval harness passes on gemma-4-12b with results recorded; `./test.sh` and CI
green throughout.
