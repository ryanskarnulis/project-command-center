# TODO

Outstanding work for Project Command Center. No sprint numbers — everything below
is **backlog**, grouped by theme. The single exception is the **current focus**,
which lives in `CURRENT.md`. Completed work is
archived in `DONE.md`.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Current focus

**Cleaning & hardening — manual review (round 2)** — findings from a full browser-driven QA
pass of the running app on 2026-06-23 (drove every route in headless chromium; exercised
drag, AI, and form flows). Triaged by severity. Fold these in before the cleanup work is
committed/wrapped. _Severity: (high) user-facing breakage · (med) bug or confusing state ·
(low) polish/docs._

- [ ] **(high) Project "Timeline" tab is a dead link → app error page.** Every project page
      (`features/projects/ProjectTabs.tsx`) still renders a **Timeline** tab linking to
      `/projects/:id/timeline`, a route removed with the Gantt in commit `04dea44`
      ("9: removed gantt"). Clicking it lands on React Router's default *"Unexpected
      Application Error! / 404 Not Found / 💿 Hey developer"* page. Repro: open any project →
      click Timeline. The removal commit cleaned the sidebar nav, routes, services, schemas,
      and tests but missed this tab — drop the `NavLink`.
- [ ] **(med) No app-level error boundary / catch-all route.** `routes/AppRoutes.tsx` defines
      no `errorElement` and no `*` catch-all, so any unknown URL or thrown route error shows
      the developer-facing default page to users. Repro: visit `/anything`. Add a friendly
      `errorElement` (404 + recover link) and/or a `*` route. This is what makes the Timeline
      bug above so ugly.
- [ ] **(med) `formatDuration(0)` renders "0 weeks".** `utils/duration.ts` `splitDuration`
      checks `minutes % WEEK === 0` first, which is true for 0, so any zero-minute duration
      prints "0 weeks". Seen as "**0 weeks** planned of 6 hours capacity" in the Today
      summary whenever nothing is scheduled. Special-case 0 → "0m".
- [ ] **(med) Today empty-state copy contradicts the overflow list.** When 0 tasks fit but
      overflow > 0, `features/today/TodayPage.tsx` shows *"No open tasks to schedule for this
      day"* directly above a populated **"Didn't fit (N)"** section. Repro: /today with
      capacity below the top-ranked task's estimate. When overflow > 0 the copy should say
      something like "Nothing fit today's capacity — see below."
- [ ] **(low) Greedy day-packing can read as a fully empty day — second look.**
      `services/today.py` `_pack` intentionally stops at the first task that doesn't fit and
      overflows the rest, so one oversized high-rank task (e.g. a 12h item under 6h capacity)
      leaves smaller sub-capacity tasks unscheduled and the day showing 0 scheduled.
      Documented as intended, but reads as broken; consider a hint ("your top task exceeds
      capacity") or backfilling smaller tasks.
- [ ] **(low) README sprint log is stale re: the removed Gantt.** README sprints 17–23 still
      document the planning/Gantt feature (`/planning`, `/projects/:id/timeline`,
      `GET /api/projects/{id}/gantt`, what-if, zoom) as `[DONE]`; the removal commit updated
      CURRENT.md / TODO.md / CLAUDE.md but not README. Per CLAUDE.md's "done" criteria, the
      sprint status should reflect the removal.
- [ ] **(low) No persistent nav to Today / Calendar / Inbox / Tasks.** Sidebar primary nav is
      only Command Center · Projects · Training; the other four routes are reachable only via
      dashboard cards or direct URL. May be intentional (dashboard-as-hub) — worth a second
      look.
- [ ] **(low) Inert placeholder controls shipped in the UI.** "Customize Command Center" and
      "Ask AI" (dashboard) plus "AI Assistant / Templates / Integrations / Help & Support"
      (sidebar) render disabled. Consider hiding until built to avoid dead clicks.

**Verified clean (no action):** all routes render with no console/network errors except the
dead Timeline link above; Kanban drag works and the parent-task + blocked-dependency guards
both hold (blocked→Done is rejected with a toast); AI capture review, project summary (live
gemma4:e2b call), and the `/new` `/done` command palette all work; task-form validation
(required title, invalid estimate) surfaces inline errors and creates nothing; soft-delete /
Trash counts reconcile with the sidebar badge; `npm run build` (tsc) is green. _Not yet
exercised (left for a deeper pass): Trash restore/purge round-trips, recurrence series
actions, alias CRUD, project description unsaved-changes blocker._

**Prior sprint (closed):** Cleanup & hardening — both remaining items (rate-limit coverage
parity across the model-calling routes, and narrowing the summary route's exception
handling) shipped; see `DONE.md`.

---

## Backlog

*(Feature work — do not promote until the hardening sprint above is closed.)*

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

- [ ] `/tasks` command — lists open tasks (optionally filtered to a project) without
      opening the web UI. Calls a new `GET /api/discord/tasks` endpoint (shared-secret
      guarded, same pattern as `/api/discord/inbox`). Bot formats results as a short
      numbered list in the reply.
- [ ] `/done <task search>` command — fuzzy-match a task title from the bot and mark it
      workflow_status=`done`. Backend: use the recurrence-preserving
      `POST /api/tasks/{id}/done` endpoint after resolving the task; add a
      `GET /api/discord/tasks/search?q=` helper for the bot to resolve the title to an ID
      first. If multiple matches, bot replies with a disambiguation list.

### Deferred infra

- [ ] `docker-compose.yml` — backend + frontend in containers (deferred: "clean restarts,
      not prod").
- [ ] litestream continuous replication instead of cron backups.

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
