# Current focus

**No active checkout.** The Agent UX epic (polish + ambient entry; PCC tasks
#291, #292, #289, #293) completed 2026-07-11 and is archived in `DONE.md` —
all four slices merged (#47–#50) plus follow-ons (#51, #52), and all four
prod tasks verified done.

Standing decisions carried forward:

- **Voice (#287) + personality (#290) stay deferred as a pair** toward a
  shared workspace-level companion layer (one system serving chess + PCC +
  future apps). This now has a concrete plan: `../AGENTS-MASTER-PLAN.md`
  (workspace root, 2026-07-11) — PCC's agent stack is the reference
  implementation of the workspace agent standard, personalities become
  layered (global + app), and a master "conductor" app delegates to per-app
  agents over a standardized REST contract. The personality/voice design
  checkout should follow that plan's Phase 0/1.
- **Non-streaming v1 stands** — SSE only if the inline entry makes the
  synchronous wait feel bad (decision recorded in the loop epic).
- **llama-swap phase 3** (retiring host Ollama) — separate chore once the
  quiet week on `journalctl -u ollama` completes (counted from 2026-07-10).

Next checkout candidates: agent-standard Phase 0/1 from the master plan
(PCC alignment: layered personality, `app.yaml` agent block, delegate-API
contract match), or backlog items in `TODO.md`.
