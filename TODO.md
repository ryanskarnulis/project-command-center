# TODO

Outstanding work for Project Command Center. No sprint numbers — everything below
is **backlog**, grouped by theme. The single exception is the **current focus**,
which lives in `CURRENT.md`. Completed work is
archived in `DONE.md`.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Current focus

**Deployable app — Discord follow-ups, improvement sweep, docker-compose,
litestream** (committed 2026-07-03). Four slices tracked in `CURRENT.md`:
(1) Discord `/tasks` + `/done`, (2) remaining round-5 improvement ideas,
(3) docker-compose deployment, (4) litestream replication. The previous UI/UX
revamp epic is archived in `DONE.md`. The round-5 hardening pass's deferred
performance notes carry below under "Deferred hardening notes".

### Deferred hardening notes (from round 5 — record now, act when warranted)

- [ ] **(low) Rollup engine loads the whole task table per request** — `_children_map`
      runs on every task list *and* every single-task read via `_read(s)_with_blocked`;
      scope it to the requested subtree or memoize once task counts grow.
- [ ] **(low) Indexes for hot task filters** — `project_id`, `review_status`,
      `workflow_status`, `deleted_at`, `recurrence_id` are all table scans today.
- [ ] **(low) Pagination for `GET /api/tasks` and `GET /api/inbox`** — both unbounded;
      trash/pending lists are already capped.

### Improvement ideas (nice-to-have — not blockers)

*Promoted to `CURRENT.md` (2026-07-03).* The 2026-07-03 audit found four of the
seven already shipped (recurring checklist tasks, restore-with-context on
`/trash`, explicit Save + dirty indicator, inline alias duplicate feedback —
see `DONE.md`). The remaining three (next-occurrence date on the repeat badge,
skip-from-list/Today/series, bulk select on `/trash`) plus the optional
alias-match-visibility stretch are Slice 2 of the current epic.


---

## Backlog

*(Feature work — unprioritized, theme-grouped.)*

### Command Bar / Search

- [ ] **Command-bar AI chat** — the third future use of the generic input: route a
      leading natural-language query (or a dedicated verb) through `ai/gateway.py`. The
      slash-command seam (`parseCommand` + ActionRows) is in place to hang this off.

### Today / Daily Schedule

- [ ] **AI reordering with a "why this order" rationale** — future slice on top of the
      deterministic plan: send the ranked plan through `ai/gateway.py` for an optional
      reorder + brief rationale, still guarded by the Python scheduler (suggestions only).
- [ ] **Calendar-aware scheduling** — schedule around meetings once calendar sync is
      unblocked (currently on the README "do not build" list — revisit when ready).

### Features

### Discord (follow-ups)

*Promoted to `CURRENT.md` (2026-07-03) — Slice 1 of the current epic
(`/tasks` + `/done` commands with their backend endpoints).*

### Deferred infra

*Promoted to `CURRENT.md` (2026-07-03) — Slices 3–4 of the current epic
(docker-compose deployment, litestream replication).*

### Nice-to-have

- [ ] Task due-date reminders
- [ ] Dark mode
- [ ] Export tasks to markdown

---

## Custom Model Training *(gated on 200+ `ai_training_examples` rows — the north star)*

- [ ] Export `ai_training_examples` to JSONL training format
- [ ] `training/unsloth/train_task_extractor.py` — Unsloth fine-tune script
- [ ] Evaluate fine-tuned model against eval suite
- [ ] `backend/app/ai/providers/llamacpp.py` — llama.cpp HTTP provider
- [ ] Update `profiles.yaml` to use `llamacpp` provider + new model
- [ ] Regression test: eval suite still passes with custom model
- [ ] Update README: note model swap, new dev commands
