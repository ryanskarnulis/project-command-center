# Sprint 3 — Discord Bot

> Goal: a Discord `/inbox` slash command triggers **the same extraction workflow** as the
> web app, from a second entry point. No new logic — the bot is a thin transport that
> HTTP-POSTs text to a new local backend route, which creates an inbox item
> (`source=discord`), runs the existing extraction workflow, and returns a summary the bot
> echoes back. Candidates are reviewed in the **web** app (no Discord buttons — "do not
> build yet"). Slice is done when: shared-secret-guarded route works (curl + Pydantic +
> 422 guard), happy-path pytest passes (gateway mocked), logs carry request IDs, the bot
> process drives it manually end-to-end, README/TASKS updated. No schema change → no migration.

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

## Design decisions (locked for this sprint)
- **Add `discord.py>=2.3`** as a runtime dep (the README's stated, actively-maintained choice).
  Confirmed with the user — only new dependency this sprint.
- The route does **create + extract in one call** and returns a summary (per the README
  Discord MVP flow). Review still happens in the web app; **no accept/reject from Discord**.
- **Shared-secret header (`X-Backend-Secret`) is the protection**, compared constant-time with
  `hmac.compare_digest`. The `api_host` bind is left as-is — the user runs `0.0.0.0` for LAN,
  which is the explicit "unless told otherwise" override of the constitution's 127.0.0.1 rule.
  Empty secret = route disabled (503).
- Shared-secret dependency takes `settings` via `Depends(get_settings)` (not an inline call)
  so tests can override it; it lives in `routes_discord.py` for now (its only consumer).

## Carried over (already done — do not rebuild)
- [x] `InboxSource.discord` enum value already exists in `db/models.py`.
- [x] `inbox_service.create_inbox_item(db, raw_text=, source=)` — idempotent by SHA-256 hash.
      Re-sending the same text returns the existing item, no duplicate candidates.
- [x] `extract_workflow.extract_tasks(db, inbox_item)` — idempotent by `processed_at`,
      Pydantic-validated, writes a failure training row + raises on bad output.
- [x] `ValidationError → 422` pattern in `routes_inbox.py:51-70` — copy it.
- [x] `httpx` already in `pyproject.toml` (bot's HTTP client) — no new dep there.
- [x] `DISCORD_BOT_TOKEN` + `BACKEND_SHARED_SECRET` placeholders already in `.env`/`.env.example`.
- [x] `RequestIDMiddleware` already wraps all `/api` routes → request-ID logs for free.

## Backend — config & dependency
- [x] `pyproject.toml` — add `"discord.py>=2.3"` to `[project].dependencies`. (installed 2.7.1)
- [x] `config.py` — add to `Settings`: `backend_shared_secret: str = ""`,
      `discord_bot_token: str = ""`, `backend_base_url: str = "http://127.0.0.1:8000"`.

## Backend — route & schema
- [x] `schemas/discord.py` (new) — `DiscordInboxRequest(raw_text)` and
      `DiscordInboxResponse(inbox_item_id, summary|None, project_hint|None, task_titles[],
      candidate_count, needs_review)`.
- [x] `api/routes_discord.py` (new) — `APIRouter(prefix="/discord", tags=["discord"])`:
      - `require_shared_secret` dependency: `settings: Settings = Depends(get_settings)` +
        `X-Backend-Secret` header → 503 if secret unset, 401 if missing/mismatch
        (`hmac.compare_digest`).
      - `POST /discord/inbox` (depends on `require_shared_secret` + `get_db`):
        `create_inbox_item(source=discord)` → `extract_tasks` (same `ValidationError → 422`
        guard as inbox) → build `DiscordInboxResponse` → `logger.info("discord_inbox_processed", …)`.
- [x] `main.py` — mount `routes_discord.router` on `api_router` alongside the existing three.

## Backend — bot process (`app/integrations/discord/`)
- [x] `integrations/__init__.py` + `integrations/discord/__init__.py` (package scaffolding;
      dirs currently empty).
- [x] `integrations/discord/commands.py` — `/inbox <text>` slash command; `httpx.AsyncClient`
      `POST {backend_base_url}/api/discord/inbox` with `X-Backend-Secret` header +
      `{"raw_text": text}`; format reply (summary line, project hint, bulleted task titles,
      "review in the app" nudge); handle non-200 (401/422/503/network) with a clear message.
- [x] `integrations/discord/bot.py` — discord.py bot, reads `discord_bot_token`, registers the
      command, syncs slash commands on ready, runs. Entry: `python -m app.integrations.discord.bot`.
      Empty token → log + exit cleanly (don't crash).

## Backend — tests (`tests/test_routes_discord.py`, new)
> Follow `test_routes_inbox.py` patterns (monkeypatch `gateway.complete`, reuse `client`/`db_session`).
> Override the secret: `app.dependency_overrides[get_settings] = lambda: Settings(backend_shared_secret="test-secret")`.
- [x] Happy path: valid header + mocked good output → 200; response carries
      summary/project_hint/task_titles; inbox row persisted with `source=discord`, tasks `candidate`.
- [x] Idempotency: same `raw_text` twice → same `inbox_item_id`, no duplicate candidates.
- [x] Wrong/missing secret → 401 (no inbox row created).
- [x] Secret unset → 503.
- [x] Mocked malformed output → 422 + failure `ai_training_examples` row.
- [x] `bot.py`/`commands.py` are **manual-tested only** (need a real token/app/guild).
      (import + command-registration smoke check passes: `build_bot()` registers `/inbox`.)

## Docs
- [x] `README.md` — flip Sprint 3 to `[DONE]`; note `BACKEND_SHARED_SECRET` is now required
      to enable the route (bot dev command already present at line 354).
- [x] `TASKS.md` — check off the Sprint 3 items.
- [x] `.env.example` — drop "leave blank for now" from the Discord comment;
      add `BACKEND_BASE_URL=http://127.0.0.1:8000`.

## Verification
**Automated:**
```
cd backend && pytest tests/test_routes_discord.py -v
cd backend && pytest                 # full suite still green
cd backend && mypy app && ruff check app
```
**Manual (needs a real Discord app + bot token + test guild):**
1. Set `BACKEND_SHARED_SECRET` + `DISCORD_BOT_TOKEN` in `backend/.env`.
2. `ollama serve`; `cd backend && python -m app.main`.
3. Curl smoke (no Discord): `curl -X POST http://127.0.0.1:8000/api/discord/inbox
   -H "X-Backend-Secret: <secret>" -H "Content-Type: application/json"
   -d '{"raw_text":"finish firewall cleanup by Friday"}'` → 200 w/ summary; 401 without header.
4. `cd backend && python -m app.integrations.discord.bot`; in Discord run
   `/inbox finish firewall cleanup by Friday` → bot replies with extracted task titles.
5. Open the web app inbox/review queue → the Discord-sourced candidates are there to review.

## Done check
_(fill in once the slice is verified end-to-end — e.g. "verified via curl + bot on guild X, inbox #N")_
