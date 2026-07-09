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

## Slice 1 — Strip the AI subsystem + training pipeline (BE + FE + migration)

The big one. Everything model-related goes.

- [ ] Delete `backend/app/ai/` entirely (gateway, providers, prompts, profiles,
      schemas, workflows, evals) and `routes_ai.py`.
- [ ] Delete `services/breakdown.py`, `services/training_data.py`,
      `services/eval_history.py`, and `routes_training.py`.
- [ ] Settings: remove profile/prompt editing, eval runs, and Ollama health from
      service, routes, and the frontend Settings page. What survives of Settings
      is app-level config only; if nothing meaningful survives, remove the
      feature and note it here.
- [ ] Frontend: delete `features/training/`; remove training-meter surfaces and
      nav entries.
- [ ] Delete the repo-root `training/` directory.
- [ ] Alembic migration: drop `ai_training_examples` and `eval_runs`.
- [ ] Infra: remove Ollama from `main.sh`, `.env` examples, docker docs, and the
      `OLLAMA_*` settings; remove `--ai-evals` from `test.sh` and CI notes;
      remove the Ollama-route rate limits (keep the rate-limit module itself —
      the agent endpoints will want it).
- [ ] Docs: excise the AI subsystem / training-table / roadmap sections from
      `README.md`; drop the legacy-AI rules from `CLAUDE.md`.

## Slice 2 — Strip inbox + Discord (BE + FE + migration)

- [ ] Delete `routes_inbox.py`, `routes_discord.py`, `services/inbox.py`, and
      `backend/app/integrations/discord/`.
- [ ] Frontend: delete `features/inbox/` (including `InboxCapturePanel` on the
      dashboard — replace with a plain quick-add task form if the dashboard
      needs a mutation surface, else remove and drop the `onTasksChanged`
      plumbing).
- [ ] Alembic migration: drop `inbox_items`.
- [ ] Infra: remove the Discord compose profile, `DISCORD_*` env vars, and
      `BACKEND_SHARED_SECRET` (nothing else uses it).
- [ ] Decide: with no AI extraction and no inbox, nothing produces
      `review_status="candidate"` tasks. Either collapse `review_status`
      (schema + service simplification, index rework) in this slice or record
      it as an explicit follow-up in `TODO.md` — don't leave it undecided.
- [ ] Docs: remove Discord setup/network sections from `README.md`; trim the
      Discord rules from `CLAUDE.md`.

## Slice 3 — Strip the calendar (BE + FE)

- [ ] Delete `routes_calendar.py`, `services/calendar.py`, and
      `frontend/src/features/calendar/` + nav entry.
- [ ] Check first: does Today or the dashboard import any calendar date logic?
      Relocate before deleting, don't reimplement.
- [ ] No schema change expected (calendar reads the `tasks` table).

## Slice 4 — Post-strip sweep

- [ ] `README.md` full pass: intro, stack, architecture diagram, repo layout,
      schema section, dev commands all describe only what exists.
- [ ] `CLAUDE.md` pass: remove the strip-era transition rules; the constitution
      describes the simple core + agent direction only.
- [ ] Dead-config hunt: `.env.example`s, `docker-compose.yml`, `app.yaml`,
      unused deps in `pyproject.toml`/`package.json` (regenerate
      `requirements.lock` if backend deps change).
- [ ] Grep for stragglers: `ollama`, `inbox`, `discord`, `training`, `calendar`,
      `candidate` across backend, frontend, scripts, and docs.

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
