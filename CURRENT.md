# Current focus

**Checked out 2026-07-12: backlog features + one overdue decision.** The
fleet agent-standard alignment completed 2026-07-11 and is archived in
`DONE.md` — `app.yaml` agent block (#54), layered Glitch personality (#55),
`X-Agent-Actor` delegate attribution (#56). With the agent stack settled
(master plan Phases 0–3 complete as of 2026-07-12,
`../agent-standard/AGENTS-MASTER-PLAN.md`), this checkout returns to the
non-agent backlog:

1. **[M] Task due-date reminders** — the longest-standing non-agent backlog
   feature. The shipped surfaces give natural delivery channels — the
   dashboard signal strip, the agent conversation, or a notification hook;
   pick one during design, don't build all three.
2. **[S] Export tasks to markdown** — small and self-contained; pairs with
   the agent tool registry (an export tool could join the 25) and with the
   deploy-from-clean-clone workflow, where a text export is the cheap
   portability story.
3. **[decision] The Tasks page's fate** — the 2026-07-10 deferral was
   "re-evaluate once the Phase 2 agent surfaces settle real usage"; that
   trigger has now fired. Decide per the `TODO.md` entry: if the
   cross-project filter/list view stays unused, delete `TasksPage` and its
   filter machinery wholesale (rule 4 of definition-of-done), keeping
   `/tasks/:id` detail routes alive for search and deep links.

Next up after this checkout: **eval-harness expansion** — a delegate-actor
end-to-end scenario (#56 shipped with unit tests only; no eval yet asserts a
conductor-attributed run's audit invariants) and a search-frugality tripwire
for `honest_about_missing`, which the recorded baseline shows over-searching
(up to ~10 turns) before conceding.

Standing decisions carried forward:

- **Personality (#290) shipped** 2026-07-11 via the agent-standard layering
  (#55) — global Glitch vendored, no app flavor. **Voice (#287) shipped** 2026-07-12 via the fleet voice standard
  (`../agent-standard/voice.md`, VOICE-PLAN Phase 3, PRs #58–#60): backend
  SpeechClient + /api/voice, vendored chess voice modules in the chat panel
  (push-to-talk + hands-free), and voice entry on the ambient search bar.
- **Non-streaming v1 stands** — SSE only if the inline entry makes the
  synchronous wait feel bad (decision recorded in the loop epic).
- **llama-swap phase 3** (retiring host Ollama) — separate chore once the
  quiet week on `journalctl -u ollama` completes (counted from 2026-07-10,
  so ~2026-07-17).
