# TODO

The backlog, grouped. One task = one branch = one PR; finished work moves to
`DONE.md` with the date.

## Next

- [ ] **Task due-date reminders** [M] — the longest-standing feature ask.
      Delivery channels exist (dashboard signal strip, agent conversation, or
      a notification hook); pick one during design, don't build all three.
- [ ] **Export tasks to markdown** [S] — small and self-contained; could also
      join the agent tool registry.

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
