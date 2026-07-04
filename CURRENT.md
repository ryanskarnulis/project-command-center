# Current focus

**Epic: Deployable app — Discord follow-ups, improvement sweep, docker-compose,
litestream** (checked out 2026-07-03).

Goal: get the app to a state where it can be deployed on other devices, and
close out the Discord follow-ups, the remaining improvement ideas, and the
deferred infra items from `TODO.md`. Everything else in the backlog stays put.

Four slices, in order. Each slice is a commit stop with a one-line message.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Improvement-ideas audit (done 2026-07-03)

Of the seven round-5 improvement ideas, four already shipped and are archived in
`DONE.md`:

- [x] **Recurring "checklist" tasks** — `_maybe_spawn_recurring_checklist` clones
      the subtree on completion (shipped 2026-06-24).
- [x] **Restore-with-context on `/trash`** — project cards show
      `archived_task_count` and offer to restore the rehomed tasks together.
- [x] **Explicit Save + dirty indicator** — dirty-state chunk shipped (Save
      disabled when clean, "unsaved" dot, `beforeunload` guard).
- [x] **Alias "already added" feedback** — inline duplicate check as you type
      (`aliasIsDuplicate` in `ProjectDetailPage`), backed by the normalized-alias
      unique index.

The three that remain are Slice 2 below. "Surface which aliases recently
matched" (the optional second half of the alias idea) is a stretch item — do it
only if Slice 2 lands small.

---

## Slice 1 — Discord follow-ups: `/tasks` and `/done` (done 2026-07-03)

Backend + bot, same shared-secret pattern as `/api/discord/inbox`. No AI calls,
so no eval case and no rate limiting needed; keep the existing Ollama-route rate
limits untouched.

- [x] `GET /api/discord/tasks` — open tasks (accepted, not done, not deleted),
      optional `?project=` filter matched by name/alias; shared-secret guarded.
      Returns id, title, project name, due date — enough for a short list.
- [x] `GET /api/discord/tasks/search?q=` — fuzzy title match over open tasks,
      returns ranked candidates for the bot to disambiguate.
- [x] `/tasks [project]` bot command — numbered list reply (cap ~10, note the
      remainder count).
- [x] `/done <task search>` bot command — resolve via the search endpoint;
      exactly one match → `POST /api/tasks/{id}/done` (the recurrence-preserving
      endpoint); multiple matches → numbered disambiguation reply; zero → "no
      match" reply. No writes on ambiguity.
- [x] pytest: both endpoints (auth required, filter, search ranking, done via
      recurrence-preserving path); structured logs with request IDs.
- [x] README Discord section: document the two new commands.

**Commit stop 1.** Implemented: exact name/alias resolution via
`find_project_by_name_or_alias`; search reuses `search_open_tasks` (existing
escaped-`LIKE` + `_text_tier` ranking, filtered to accepted + not-done). 8 new
tests in `test_routes_discord.py`; full backend suite green (334).

## Slice 2 — Remaining improvement ideas (frontend-heavy)

- [ ] **Next occurrence next to the repeat badge** — "Every week · next Jul 1"
      on `TaskCard`'s repeat badge (and task detail). Next-date math already
      lives in `services/task_recurrence.py`; expose it on the task read payload
      rather than duplicating scheduling math in TS.
- [ ] **Skip an occurrence from where the task shows up** — skip action on the
      task list card menu, Today page, and the series timeline (today it lives
      only on the task detail page). Reuse the existing skip endpoint + confirm
      pattern; mark-done from cards already shipped (complete circle, sprint 23).
- [ ] **Bulk select on `/trash`** — checkboxes + select-all per section,
      multi-restore / multi-purge using the existing per-item iteration in
      `useTrash` (409-tolerant, restored-vs-skipped reporting).
- [ ] *(stretch)* Alias match visibility — surface which alias matched on a
      triaged inbox note. Skip if the slice is already at a healthy size.
- [ ] Vitest for the new UI states; happy-path pytest if the task payload gains
      the next-occurrence field.

**Commit stop 2.**

## Slice 3 — docker-compose deployment

The deployability core. Ollama stays on the host (GPU); containers reach it
over the host gateway. SQLite lives on a bind-mounted `data/` volume.

- [ ] `backend/Dockerfile` — Python 3.11+, install from `requirements.lock`,
      entrypoint runs `alembic upgrade head` then uvicorn.
- [ ] `frontend/Dockerfile` — `npm ci` + `vite build`, served by nginx with
      `/api` reverse-proxied to the backend container (keeps the derive-from-host
      API URL behavior irrelevant in prod; no CORS config needed).
- [ ] `docker-compose.yml` — `backend`, `frontend`, optional `discord-bot`
      service behind a compose profile (only starts when tokens are set),
      healthchecks, `.env`-driven config, `data/` volume,
      `OLLAMA_BASE_URL=http://host.docker.internal:11434` via
      `extra_hosts: host-gateway`.
- [ ] **Settings-write guard decision** — behind nginx the backend sees the
      proxy's container IP, so the loopback check in `api/guards.py` would 403
      all Settings writes. Plan: nginx sets `X-Forwarded-For`; guard gains an
      opt-in `TRUSTED_PROXY_IPS` setting (empty default = current behavior
      exactly) and only then trusts the forwarded client IP. Settings writes
      remain loopback-clients-only in both modes.
      *Assumed this is the right call because README already names
      trusted-proxy handling as the prerequisite — change if you'd rather keep
      Settings writes host-direct-only and skip the config knob.*
- [ ] Verify end-to-end on a clean checkout: `docker compose up` → capture an
      inbox note through the LAN UI → task lands in DB → visible in UI.
      Settings writes verified 403 from LAN, allowed per the guard decision.
- [ ] README: new "Deploy with Docker" section (compose commands, env vars,
      volume/backup notes, Ollama-on-host requirement); note that `main.sh`
      remains the dev path.

**Commit stop 3.**

## Slice 4 — litestream continuous replication

- [ ] `litestream.yml` — replicate `data/app.db`; default target a local second
      path (e.g. mounted `data/replica/` or NFS), S3-compatible target left as a
      commented example. No new cloud dependency by default.
- [ ] `litestream` sidecar service in `docker-compose.yml` (official image,
      same `data/` volume); short doc note for running it via systemd in the
      non-docker setup.
- [ ] Keep `scripts/backup_db.sh` as the manual/cron snapshot path — litestream
      complements it, doesn't replace it (README states this).
- [ ] **Restore drill** — actually run `litestream restore` to a scratch path
      once and diff row counts against the live DB; document the restore
      procedure in README.
- [ ] README backups section updated.

**Commit stop 4.**

---

## Out of scope (stays in TODO.md / do-not-build)

- Deferred hardening notes (rollup engine scan, indexes, pagination) — record
  stands; act when task counts warrant.
- Command-bar AI chat, Today AI reordering, calendar-aware scheduling.
- Multi-user auth, reverse-proxy-on-the-internet hardening — deployment target
  is still a trusted home LAN.
- Custom model training track (gated on 200+ examples).

## Definition of done for the epic

Every slice meets the CLAUDE.md definition of done (manual vertical path,
happy-path pytest, structured logs, README updates). The epic is done when a
clean machine with Docker + Ollama can `git clone`, set `.env`, and
`docker compose up` into a working app with continuous DB replication — and the
Discord bot answers `/inbox`, `/tasks`, and `/done`.
