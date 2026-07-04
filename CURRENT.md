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

- [X] **Next occurrence next to the repeat badge** — "Every week · next Jul 1"
      on `TaskCard`'s repeat badge (and task detail). Next-date math already
      lives in `services/task_recurrence.py`; expose it on the task read payload
      rather than duplicating scheduling math in TS.
- [X] **Skip an occurrence from where the task shows up** — skip action on the
      task list card menu, Today page, and the series timeline (today it lives
      only on the task detail page). Reuse the existing skip endpoint + confirm
      pattern; mark-done from cards already shipped (complete circle, sprint 23).
- [X] **Bulk select on `/trash`** — checkboxes + select-all per section,
      multi-restore / multi-purge using the existing per-item iteration in
      `useTrash` (409-tolerant, restored-vs-skipped reporting).
- [X] *(stretch)* Alias match visibility — surface which alias matched on a
      triaged inbox note. Skip if the slice is already at a healthy size.
- [X] Vitest for the new UI states; happy-path pytest if the task payload gains
      the next-occurrence field.

**Commit stop 2.**

## Slice 3 — docker-compose deployment

The deployability core. Ollama stays on the host (GPU); containers reach it
over the host gateway. SQLite lives on a bind-mounted `data/` volume.

- [x] `backend/Dockerfile` — Python 3.11+, install from `requirements.lock`
      (`pip install -e . -c requirements.lock`, no `[dev]`), CMD runs
      `alembic upgrade head` then uvicorn (single worker, no reload).
- [x] `frontend/Dockerfile` — `npm ci` + `vite build`, served by nginx with
      `/api` reverse-proxied to the backend container. Built with an empty
      `VITE_API_URL` so the client emits relative `/api` paths (same-origin, no
      CORS). `nginx.conf` adds SPA fallback + a 200s proxy timeout for AI routes.
- [x] `docker-compose.yml` — `backend`, `frontend`, optional `discord-bot`
      service behind the `discord` compose profile, healthcheck on `/health`,
      `.env`-driven config, `./data` volume,
      `OLLAMA_BASE_URL=http://host.docker.internal:11434` via
      `extra_hosts: host-gateway`. Dashboard is host-only by default
      (`FRONTEND_BIND=127.0.0.1`), LAN a one-line opt-in. Fixed compose subnet so
      `TRUSTED_PROXY_IPS` has a deterministic value. Backend publishes no host port.
- [x] **Settings-write guard decision** — behind nginx the backend sees the
      proxy's container IP, so the loopback check in `api/guards.py` would 403
      all Settings writes. Plan: nginx sets `X-Forwarded-For`; guard gains an
      opt-in `TRUSTED_PROXY_IPS` setting (empty default = current behavior
      exactly) and only then trusts the forwarded client IP. Settings writes
      remain loopback-clients-only in both modes.
      *Refined per follow-up: Settings writes now work **from the host by default**.
      Docker's NAT means no external client presents as loopback, so instead of
      trusting a forwarded loopback IP (spoofable), the guard trusts writes the
      nginx proxy forwards **only while the dashboard is bound host-only** — then
      the LAN can't reach nginx, so every forwarded request is from the host.
      Exposing the dashboard (`FRONTEND_BIND=0.0.0.0`) auto-re-guards writes to 403.
      Implemented in `app/api/request_ip.py` (`is_trusted_proxy`,
      `proxy_is_host_only`, spoof-resistant rightmost-XFF `resolve_client_ip`) +
      `TRUSTED_PROXY_IPS`/`FRONTEND_BIND` settings; compose passes both to the
      backend. Verified live in both modes (host write 200 / LAN write 403).*
- [x] Verify end-to-end: built both images, brought the stack up (scratch data
      volume), confirmed backend healthy + migrations ran, nginx serves the SPA
      with client-route fallback, `/api` proxied to the backend, `host.docker.internal`
      reaches host Ollama, and POST inbox → process extracted two tasks into the DB.
      Host Settings write through nginx returns 200 (host-only default); with
      `FRONTEND_BIND=0.0.0.0` the same write returns 403 (incl. spoofed XFF);
      reads 200. Data survived a backend restart (volume). Full backend suite green
      (359). *Verification surfaced two real deploy bugs, both fixed: empty
      `DISCORD_GUILD_ID=` crashed startup (added `env_ignore_empty=True`), and the
      slim image has no `curl` (README admin snippet uses Python).*
- [x] README: new "Deploy with Docker" section (compose commands, env vars,
      volume/backup notes, Ollama-on-host requirement); notes that `main.sh`
      remains the dev path.

**Commit stop 3.**

## Slice 4 — litestream continuous replication (done 2026-07-03)

- [x] `litestream.yml` — replicate `data/app.db`; default target a local second
      path (e.g. mounted `data/replica/` or NFS), S3-compatible target left as a
      commented example. No new cloud dependency by default.
- [x] `litestream` sidecar service in `docker-compose.yml` (official image,
      same `data/` volume); short doc note for running it via systemd in the
      non-docker setup.
- [x] Keep `scripts/backup_db.sh` as the manual/cron snapshot path — litestream
      complements it, doesn't replace it (README states this).
- [x] **Restore drill** — actually run `litestream restore` to a scratch path
      once and diff row counts against the live DB; document the restore
      procedure in README.
- [x] README backups section updated.

**Commit stop 4.** Default-on `litestream/litestream:0.3` sidecar sharing the
`./data` mount, `command: replicate` against `litestream.yml` (file replica at
`data/replica/`, WAL prerequisite already met — the app runs SQLite in WAL mode).
S3 target left commented in `litestream.yml` with `LITESTREAM_S3_*` stubs in
`.env.example`; `.gitignore` now excludes `data/replica/` and litestream's
`.app.db-litestream/` shadow dir. **Restore drill run live:** brought the stack
up, created a project through the API *after* the initial snapshot, then
`litestream restore`d the file replica to a scratch path — the post-snapshot
project was present and all ten tables' row counts matched the live DB
(tasks 152, projects 12, activity_events 698, ai_training_examples 79, …),
proving the WAL stream round-trips, not just the snapshot. No Python/schema/AI
touched, so no migration or eval case; backend suite still green.

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
