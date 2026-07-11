# Current focus

**Epic: Phase 2 — local runtime + provider layer (and MCP tool-surface
completion)** (checked out 2026-07-10).

The Phase 2 kickoff is done and archived in `DONE.md`: the dead AI-era columns
are gone, the agent design doc is merged, and the PCC MCP server ships the
service layer as tools — Claude Code is already PCC's first agent client. This
epic lands the piece everything downstream blocks on: a local llama.cpp
runtime and a provider layer to speak to it. The agent loop, chat panel, and
eval harness come in the next checkout.

**The GPU/model-sharing question is the heart of this epic.** The chess app
(`../chess`) already runs a local agent on **gemma-4-12B**
(`unsloth/gemma-4-12B-it-qat-GGUF:UD-Q4_K_XL` on llama.cpp, `--jinja` tool
calling) with strong native tool-calling, fully GPU-resident on the shared
RTX 3060 (~10.2 GB of 12 GB with MTP speculative decoding, ~94 tok/s on
structured tool calls). PCC's agent wants the *same model config*. Two apps
wanting the same weights on one GPU means the efficient answer is probably
**one shared server both apps point at — not two servers, and possibly not a
model swap at all**. That partially obsoletes `../future-plans/llama-swap.md`
(written when chess ran a 26B and PCC ran a separate e2b via Ollama); the
plan's phase-0 contention triggers have fired, but the *shape* of the fix
needs re-deciding with the same-model fact in hand.

Decisions already made (don't relitigate):

- **gemma-4-12B is the working model choice.** Proven native tool calling on
  this exact GPU in chess; revisit only if PCC's tool-calling evals fail on it.
- **OpenAI wire format for the provider** (`/v1/chat/completions` against
  llama-server), structured output via `response_format: json_schema`
  (grammar-constrained), Pydantic validation at the boundary. No cloud
  providers, per the constitution.
- **MCP follow-up tools land first** (dependencies + recurrence) — small,
  already scoped in `docs/agent-design.md`, completes the tool surface the
  agent loop will consume next checkout.

Open decision (resolved by slice 2, 2026-07-10 — see the slice for the
outcome; kept for the framing):

- **The sharing shape.** Candidates: (a) a shared workspace-level
  llama-server (`llama-swap/`-style dir or plain compose) that chess and PCC
  both reach via `host.docker.internal`; (b) llama-swap as the front door with
  a *single* model entry — swap machinery idle today, but the "one owner for
  the GPU" property still pays off when immich/voice arrive; (c) PCC pointing
  at chess's existing `llama` container — cheapest, but couples PCC's agent to
  chess's compose lifecycle and flag ownership (likely reject, but write down
  why).
- **The config must satisfy both apps.** Sampling is per-request (fine), but
  context size, KV-cache quantization, and MTP are *server-level*: chess runs
  `-c 8192`; the PCC agent loop may want more. Check whether a larger ctx
  (e.g. 16k with q8 KV) still fits alongside MTP in 12 GB, or whether MTP goes.
  Whatever wins, the flags get one owner and both repos' docs point at it.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Slices (one PR each, squash-merged on green CI)

### Slice 1 — MCP follow-up tools (dependencies + recurrence)

- [x] Tools per `docs/agent-design.md`: add/remove task dependency,
      skip/stop recurrence — same guardrails as the first pass (service layer
      only, validation at the boundary, `activity_events` stamped
      `agent:mcp`). Bonus: dependency add/remove now writes audit events from
      *every* caller (UI included) — a pre-existing gap. (#34)
- [x] Happy-path pytest per tool (monkeypatch `runtime.session_factory`, per
      the established pattern) + end-to-end verification over real stdio with
      the MCP client SDK.
- [x] Doc pass: `docs/agent-design.md` tool table, `README.md` tool list.

### Slice 2 — Shared runtime decision + stand-up

> Mostly workspace-level work outside this repo; the PCC deliverable is the
> decision record and any compose/env plumbing.

- [x] Decide the sharing shape: **option (b), llama-swap with a single
      `gemma-4-12b` entry** — the "one GPU owner" property is the durable
      part; (c) rejected for lifecycle/flag-ownership coupling. Recorded in
      `docs/agent-design.md` (runtime section); `../future-plans/llama-swap.md`
      updated (phases 0–1 done, phase 2 obsolete as written).
- [x] Stood up `../llama-swap/` (pinned `v236-cuda-b9935`, port 8200):
      ctx pushed to the model's full **128k** (`-c 131072` + q8 KV + MTP) —
      SWA keeps the KV small, so it's 9.5 GB loaded / 10.5 GB peak on the
      3060 (was 10.2 GB at 8k/f16); 125k-token needle test retrieved
      correctly; tool-call completion valid first try, ~112 tok/s shallow.
- [x] Chess cutover: PR #87 (CI green — **merge pending human review**),
      deployed from branch and verified: NL command → `make_move` through
      llama-swap, engine-only gameplay survives the brain being stopped
      (500 on `/api/command`, pre-existing for configured-but-down).
- [x] Interim fixes' fate: chess's `--sleep-idle-seconds` retired with its
      `llama` service (replaced by `ttl: 600`); PCC's `keep_alive` already
      died with the old `ai/` package. Deferred phase-3 cleanup: host Ollama
      still active (last real traffic 2026-07-07; odysseus's
      `OLLAMA_BASE_URL` is optional/unset) — retire per llama-swap.md
      phase 3 after a quiet week of `journalctl -u ollama`.

### Slice 3 — PCC provider layer

> The old `ai/` package was stripped to `__pycache__` residue; this is a
> fresh module, not a revival. Delete the stale pycache in this slice.

- [ ] `ai/providers/llamacpp.py` (or per design doc layout):
      chat-completions-with-tools against the shared server; structured
      outputs via `json_schema`; Pydantic-validated at the boundary — no
      best-effort parsing. Typed signatures, structlog with request IDs.
- [ ] `LLAMACPP_BASE_URL` in config + compose + `.env.example`
      (`host.docker.internal` + `extra_hosts` stanza, same mechanism the old
      Ollama path used).
- [ ] Tests: unit tests against a faked wire response; one opt-in
      integration test (skipped when the server is absent) exercising a real
      tool call round-trip.
- [ ] Verification: since the agent loop doesn't exist yet, prove the
      vertical path with the integration test + a documented curl/pytest
      smoke — the loop consumes this next checkout.
- [ ] Dependency check: prefer `httpx`/stdlib over an SDK; **any new
      dependency needs sign-off first** per `CLAUDE.md`.

---

## Out of scope for this epic

- Agent loop, conversation persistence, chat panel UI, RAG/retrieval, eval
  harness — next Phase 2 checkouts (`TODO.md`).
- Tasks-page decision, due-date reminders, markdown export — backlog.
- llama-swap phase 3 (retiring host Ollama) beyond noting what's left.

## Definition of done for the epic

All three slices merged; `./test.sh` and CI green; dependencies/recurrence
callable from Claude Code with audit entries; one shared GPU server serves
gemma-4-12B for both chess and PCC with the decision recorded; and PCC's
provider can complete a validated tool-call round-trip against it.
