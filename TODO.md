# TODO

The backlog, grouped. One task = one branch = one PR; finished work moves to
`DONE.md` with the date.

## Next

- [ ] **Task due-date reminders** [M] — the longest-standing feature ask.
      Delivery channels exist (dashboard signal strip, agent conversation, or
      a notification hook); pick one during design, don't build all three.
- [ ] **Export tasks to markdown** [S] — small and self-contained; could also
      join the agent tool registry.

## Focus follow-ups (from the M05i handoff)

- [ ] **Persist planned-vs-actual** [S] — the mobile clock already books a
      block's actual minutes, but only in the session log; there is no column
      and nothing surfaces it. One `actual_minutes` write on completion opens
      the whole estimate-calibration story.
- [ ] **A stored day plan** [L] — today's plan is derived and capacity-packed,
      so "Defer" leaves the day rather than landing in "Didn't fit", and
      "Schedule" has to extend the session instead of pinning one task. Both
      verbs want an explicit per-day ordering. Decide this together with
      **fixed-time commitments** (a 14:00 meeting): every block currently
      push-forwards, and the moment one is pinned the day-shape arithmetic is
      wrong. The handoff calls this the cheapest thing to decide now and the
      most expensive later.
- [ ] **Swipe discoverability** [S] — the gesture has no affordance and the
      clock chip reads as a status readout. Both are reachable from `⋯`, but a
      one-time first-session hint is the obvious next move.

## Trash follow-ups (from the M08f handoff)

- [ ] **Trash retention policy** [M] — a scheduled job that hard-deletes rows
      whose `deleted_at` is older than a window (the design assumes 30 days),
      with the window exposed to the client (config or `/api/trash/count`).
      The mobile sub-line's `removed after 30 days` and the row's `n days
      left` are dropped until this exists; without it trash grows forever.
      It is a data-loss policy for production, so it is a deliberate decision,
      not a chore.
- [ ] **Shared swipe primitive** [S] — `focus/mobile/SwipeRow` (right = done,
      left = defer), `trash/mobile/TrashSwipeRow` (right = restore, left
      dead) and `agent/mobile/ConversationSwipeRow` (left = delete, right
      dead) now carry the same pointer mechanics three times. The third route
      has arrived; extract one row gesture with configurable reveals, and
      share the first-run swipe hint from the Focus follow-ups with it.
- [ ] **Desktop `/trash` follows M08f** — desktop keeps the pre-M08f page, the
      same debt M02f and M06f took on. Decide when.

## Agent follow-ups (from the M07f handoff)

- [ ] **Stop a run** [M] — nothing cancels an agent run; the mobile clock makes
      the wait legible but not escapable. Needs the loop to honour a cancel
      between provider/tool calls and an endpoint to request it, then a
      `Stop` beside the clock.
- [ ] **Mobile `Load older …` treatment** [S] — both paging buttons are real
      and undrawn at 390px; they ship as 44px text buttons without a design
      pass.
- [ ] **Mic long-press to mute** [S] — muting a hands-free loop now costs two
      taps through the `⋯` sheet. Fine until someone runs a long voice
      session.
- [ ] **Desktop `/agent` follows M07f** — desktop keeps the pre-M07f rail and
      `window.confirm`, the same debt the other mobile passes took on. The
      undo-backed delete could move over first; it needs no new UI.

## Evals

- [ ] Delegate-actor end-to-end scenario — #56 shipped with unit tests only;
      no eval asserts a conductor-attributed run's audit invariants.
- [ ] Search-frugality tripwire for `honest_about_missing` — the baseline
      shows over-searching (up to ~10 turns) before conceding.

## Someday / conditional

- [ ] Embeddings (`sqlite-vec`, local model) — only if FTS5 retrieval proves
      insufficient; it hasn't yet.
- [ ] `_find_occurrence_on` deleted-row guard — revisit only if a
      re-completion landing on a trashed date actually bites (the skip path
      was fixed 2026-07-23; the completion path still uses the broad guard).
- [ ] Retire host Ollama (llama-swap phase 3) — still active/enabled as of
      2026-09-01; everything serves through `../llama-swap/` now, so this is
      a stop/disable/uninstall chore plus a check nothing still points at it.
