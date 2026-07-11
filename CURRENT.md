# Current focus

**Epic: Agent UX — polish + ambient entry** (checked out 2026-07-11; PCC
tasks #291, #292, #289, #293).

The agent works (loop epic, archived in `DONE.md`); this epic makes it
pleasant to use and reachable from where work happens. Replies render as
plain text today (raw markdown asterisks and all), the agent hides behind
its own page, the avatar is a stock icon, and there's an unrelated-but-real
mobile bug in the task edit modal.

Decisions already made (don't relitigate):

- **`react-markdown` is approved** (signed off 2026-07-11) for rendering
  assistant replies. Safe-by-default (no raw HTML), scoped to the agent
  bubble. No other new dependency without asking.
- **Search-bar handoff shape (#292): inline, expandable.** Enter in the
  command search posts to the agent and renders the exchange in a panel
  under the bar (reusing the panel's message components — one rendering
  surface, not two); a "Continue in Agent" affordance opens `/agent/:id`
  with the same conversation (free — conversations are persisted). Slash
  commands are removed from the search bar in the same slice: delete the
  feature fully per the constitution (parser, docs, tests), no dead config.
- **Deferred as a pair — voice (#287) + personality (#290).** Direction
  agreed with the user: stand up something *shared between projects* (chess,
  PCC, future) rather than porting per-app copies — a workspace-level
  companion layer, llama-swap-style. Needs its own design checkout; leave
  the PCC tasks open.

Open decisions (resolve in the slices, record the outcome here):

- **Entity links (#291) — RESOLVED (slice 2): no reply-text linkification.**
  Trajectory rows link from persisted ids (exact, zero prompt changes);
  text linkification would require prompting the model to emit ids, which
  risks the eval constraint (prompt tweaks must not degrade tool honesty)
  for fuzzy benefit. Revisit only if real usage shows people hunting for
  links the trajectory rows don't provide.
- **Mascot scope (#289) — RESOLVED (slice 3): static mark + working-state
  animation only.** One shared `SpiderMark` component (extracted from the
  topbar brand SVG) everywhere; a subtle CSS-only bob (1.6s ease-in-out)
  on the working indicator, guarded by `prefers-reduced-motion`. No idle
  animation — nothing else earned it.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Slices (one PR each, squash-merged on green CI)

### Slice 1 — Mobile task-edit pills bug (#293)

- [x] The task edit modal's dropdown pills (chips) flash open and close on
      mobile tap — root cause: `ChipPopover`'s scroll/resize listeners
      dismissed the popover when the soft keyboard shifted the viewport
      right after opening. Scroll/resize now re-anchor instead of closing;
      outside-press moved to capture phase (the peek panel's bubble-phase
      `stopPropagation` swallowed it, leaving popovers stuck open). Covered
      with jsdom tests for the tap sequence, keyboard scroll/resize, and
      the swallowed outside-press.
- [x] Verify on a mobile viewport with `verifier-browser` (touch events are
      exactly what Vitest can't exercise) — iPhone 13 emulation, real touch
      taps: open/stay-open, re-anchor on scroll/resize, estimate input +
      apply, outside-press dismissal.

### Slice 2 — Agent output rendering (#291)

- [x] Assistant replies render as markdown (`react-markdown@10`, agent
      bubble only, dependency locked in `package.json`); user bubbles stay
      plain text. Links open in a new tab; styles scoped under
      `.agent-message--assistant .agent-bubble`.
- [x] Tool-call trajectory rows link to what they touched via pure
      `linkFor()` (same defensive id parsing as undo): task mutations →
      `/tasks/:id`, project mutations → `/projects/:id`, trash rows →
      `/trash`; undone rows re-route (undone create → `/trash`, undone
      trash → detail page); failed calls never link.
- [x] Resolve the reply-text linkification open decision; recorded above
      (resolved: no — trajectory links suffice).
- [x] Vitest for the mapping (linkFor suite + MessageBubble/ToolCallList
      component tests); `verifier-browser` pass over a real conversation
      for the rendered surface.

### Slice 3 — Spider mascot for the agent (#289)

- [x] Replace the stock `Bot` avatar with a spider mark that reads as the
      PCC brand — the topbar SVG extracted into a shared
      `components/SpiderMark.tsx` (size prop, currentColor, aria-hidden),
      consumed by the brand, Agent nav entry, panel avatar, working
      indicator, and empty state; zero `Bot` imports remain.
- [x] Resolve the static-vs-animated open decision; recorded above
      (static + working-state bob, reduced-motion guarded).
- [x] `verifier-browser` screenshot pass (pure rendered surface).

### Slice 4 — Ambient agent entry from the search bar (#292)

- [ ] `CommandSearch`: live results keep rendering under the bar as today;
      Enter posts the text to the agent (new conversation via the existing
      API + rate limit) and renders the exchange inline under the bar with
      the same message/tool-call components as the panel.
- [ ] "Continue in Agent" opens `/agent/:id` with that conversation.
- [ ] Remove slash commands from the search bar completely (parser, its
      tests, any docs/hints) — deletion done to the constitution's standard.
- [ ] In-progress state matches the panel (working indicator, disabled
      input); errors surface inline. `verifier-browser` for the whole flow.

---

## Out of scope for this epic

- **Voice input (#287) and the personality system (#290)** — deferred
  together toward a shared, workspace-level companion layer (one system
  serving chess + PCC + future projects, like llama-swap owns the GPU).
  Next checkout candidate; needs its own design doc first.
- Streaming/SSE — only if the inline entry makes the synchronous wait feel
  bad (decision recorded in the loop epic).
- llama-swap phase 3 (retiring host Ollama) — separate chore once the quiet
  week on `journalctl -u ollama` completes (counted from 2026-07-10).
- Tasks-page decision, due-date reminders, markdown export — backlog.

## Definition of done for the epic

From the dashboard: type an ask into the search bar → inline agent exchange
with markdown-rendered reply, spider mascot working state, and clickable
links to whatever it created → continue the same conversation on `/agent`.
The mobile pills bug is dead on a real mobile viewport. Agent evals stay
6/6 on gemma-4-12b (formatting/prompt tweaks are not allowed to degrade
tool honesty — rerun before merging anything that touches the system
prompt). `./test.sh` and CI green throughout; PCC tasks #291/#292/#289/#293
completed in the prod instance as slices land.
