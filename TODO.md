# TODO

Outstanding work for Project Command Center. No sprint numbers — everything below
is **backlog**, grouped by theme. The single exception is the **current focus**,
which lives in `CURRENT.md`. Completed work is
archived in `DONE.md`.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Current focus

**Post-deploy hardening & polish** (checked out 2026-07-03). Legitimate follow-ups
from an external code review, tracked in `CURRENT.md`: (1) frontend data-consistency
polish, (2) task indexes, (3) pagination on unbounded list endpoints, (4) stretch:
rollup subtree scoping. The prior "Deployable app" epic (Discord `/tasks`+`/done`,
improvement sweep, docker-compose, litestream) is archived in `DONE.md`.

### Deferred hardening notes (from round 5)

*Promoted to `CURRENT.md` (2026-07-03) — the indexes, pagination, and rollup-scan items
are Slices 2–4 of the current epic, warranted now that the app is deployable and an
external review independently flagged them as the next ceiling.*

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
(docker-compose deployment, litestream replication). Both **done** 2026-07-03;
the whole "Deployable app" epic is now implemented across its four slices.*

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
