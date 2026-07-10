# Current focus

**Epic: The strip — pivot to a simple project manager** (checked out 2026-07-09).

## Direction change

PCC is changing direction. The AI-assisted-capture + training-data + custom-model
track is dead; so are the calendar, the inbox, and the Discord bot. What remains
is a **simple, boring, reliable local project management app**: projects, tasks
(subtasks, dependencies, recurrence), Today, search, trash, dashboard.

On top of that slimmed core, the next epic builds a **local agent** — llama.cpp
runtime, tool calling, MCP, retrieval — that operates the app *through the same
service layer the UI uses*. That work is in `TODO.md` ("Phase 2 — local agent")
and starts only after this strip epic is done.

Decisions already made (don't relitigate):

- Inbox and the Discord bot are removed, not kept as manual shells. The agent
  becomes the capture surface later.
- All training data is disposable — drop `ai_training_examples` and `eval_runs`
  without export.
- The agent will run locally on llama.cpp (shared RTX 3060; GPU-sharing story
  with the chess app's llama-server is tracked in `../future-plans/llama-swap.md`).

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Ground rules for the strip

- One slice per PR, squash-merged on green CI (normal workflow). Each slice
  deletes code **and** updates `README.md`/`CLAUDE.md` sections that described it
  — no doc debt between slices.
- Deletions are hard deletes from the tree; git history is the archive. Don't
  leave commented-out code or `_legacy` files.
- Every dropped table gets a reviewed Alembic migration. Data is disposable, but
  the migration chain must stay clean.
- After each slice: `./test.sh` green locally before push (the `--ai-evals` flag
  disappears with Slice 1).

## Slices 1 + 2 — Strip AI subsystem + training + inbox + Discord (MERGED) ✅

**Merged into one PR.** Slice 1 (AI/training) and Slice 2 (inbox/Discord) as
documented were not cleanly separable in the AI-first order: inbox and Discord
are pure AI consumers — their extraction/matching/review flow imports `app.ai`
and writes `ai_training_examples`, the table Slice 1 drops. Doing Slice 1 alone
would have forced rewriting inbox/Discord into stubs one PR before deleting them
(violating "touch that code only to delete it"). Decision (2026-07-09): strip
all four together.

- [x] Delete `backend/app/ai/` entirely (gateway, providers, prompts, profiles,
      schemas, workflows, evals) and `routes_ai.py` (its non-AI `/dashboard`
      endpoint relocated to `routes_dashboard.py`).
- [x] Delete `services/breakdown.py`, `services/training_data.py`,
      `services/eval_history.py`, `services/settings.py`, `services/review.py`,
      `services/inbox.py`, and `routes_training.py` / `routes_settings.py`.
- [x] Settings: nothing app-level survived — the feature is removed entirely
      (service, routes, and the frontend Settings page/nav).
- [x] Frontend: delete `features/training/`, `features/inbox/`,
      `features/settings/`; remove their nav entries, the training-meter and
      break-down surfaces, and the inbox capture panel (dashboard panel removed
      outright, no replacement quick-add).
- [x] Delete `routes_inbox.py`, `routes_discord.py`, and
      `backend/app/integrations/discord/`.
- [x] Delete the repo-root `training/` directory.
- [x] Alembic migration `019a9b406cae`: drop `ai_training_examples`,
      `eval_runs`, and `inbox_items` (plus the `tasks.inbox_item_id` /
      `breakdown_output_json` columns). Upgrade/downgrade round-trip verified.
- [x] Infra: remove Ollama from `main.sh`, `.env` examples, docker docs, and the
      `OLLAMA_*` settings; remove `--ai-evals` from `test.sh`; remove the
      per-route rate limits and `DISCORD_*` / `BACKEND_SHARED_SECRET` config.
      **Kept the rate-limit module and the loopback write-guard** — the Phase 2
      agent endpoints will want them (rate-limit now tested in isolation).
- [x] Docs: excised the AI subsystem / training-table / inbox / Discord / roadmap
      sections from `README.md` and the legacy rules from `CLAUDE.md`.
- [x] **`review_status` decision — DEFERRED** (recorded in `TODO.md`). Nothing
      produces `candidate` tasks now, but collapsing `review_status` touches the
      compound index and every task-listing service; it's a coherent separate
      cleanup, bundled with the leftover AI-only task columns below.

### Deferred to the tasks-table post-strip cleanup (see `TODO.md`)

The `tasks` table keeps three now-AI-only columns this PR left in place to bound
its blast radius: `review_status` (load-bearing index + pervasive service
filtering), `confidence` (extraction-only, read-only in `TaskRead`), and
`assignee_hint` (settable via the task API). Collapsing/removing them is one
follow-up.

## Slice 3 — Strip the calendar (BE + FE) ✅

- [x] Deleted `routes_calendar.py`, `services/calendar.py`,
      `tests/test_calendar.py`, `frontend/src/api/calendar.ts`, and
      `frontend/src/features/calendar/` + nav entry, route, and router include.
- [x] Dashboard `UpcomingEvents` reused the calendar feed — rewired it onto
      `listAllTasks` + `utils/dates` (client-side range filter) and dropped the
      "View calendar" link/CSS. No date logic reimplemented; Today untouched.
- [x] No schema change (calendar read the `tasks` table).
- [x] `README.md` + `CLAUDE.md` calendar references removed.

## Slice 4 — Post-strip sweep

- [x] `README.md` full pass: dropped the strip-in-progress note and removal
      narrative (history lives in git/`DONE.md`); roadmap now shows only
      Phase 2; repo layout gained `schemas/` + `refresh_design_kit.sh`;
      `review_status` bullet reworded as vestigial with the `TODO.md` pointer.
- [x] `CLAUDE.md` pass: strip-era framing removed; the "don't resurrect"
      directive and duplicate "Legacy subsystems" section dropped entirely
      (decision 2026-07-09 — scope discipline + "Do not build yet" cover it).
- [x] Dead-config hunt: removed `discord.py`, `pyyaml`, `types-PyYAML` from
      `pyproject.toml` (moved `httpx` to dev extras — test-client only),
      regenerated `requirements.lock`; rewrote the stale Settings-write-guard
      comment in root `.env.example` and the `require_local_write` docstring;
      deleted the untracked leftover `backend/app/ai/profiles.local.yaml`.
- [x] Grep for stragglers: remaining hits are intentional — alembic history,
      the deferred `review_status`/`candidate` code (`TODO.md` cleanup), and
      changelogs.

---

## Out of scope for this epic

- Anything agent-related (llama.cpp, MCP, tools, RAG) — that's Phase 2 in
  `TODO.md`, and it starts on a clean base, not in parallel.
- Multi-user auth / internet exposure — unchanged decision, trusted home LAN.

## Definition of done for the epic

All four slices merged; `./test.sh` and CI green; no route, service, feature
folder, table, env var, or doc section referring to AI, training, inbox,
Discord, or calendar; the app runs end-to-end (`main.sh` and docker) as a plain
project manager.
