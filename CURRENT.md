# Sprint 14 — Security Posture Hardening

## Why this sprint

After the AI subsystem quality pass, the strongest small slice was the security
backlog group: reduce practical LAN/local risk without adding auth, new infra, or
dependencies. This keeps the app honest about its single-user/trusted-LAN model
while closing the easiest accidental/malicious prompt-size and Discord mention
holes.

## What shipped

- `InboxRawText` in `backend/app/schemas/common.py` caps web + Discord inbox capture
  text at 8,000 characters while preserving strip + nonblank validation. Oversized
  `POST /api/inbox` and `POST /api/discord/inbox` payloads now fail Pydantic
  validation before DB writes or model calls.
- Discord `/inbox` success and error followups now pass
  `AllowedMentions.none()`, so user/model text echoed in a reply cannot ping roles
  or users.
- `README.md` now explicitly documents the accepted posture: `API_HOST=127.0.0.1`
  is safest/default; `API_HOST=0.0.0.0` exposes normal read/write app APIs to a
  trusted LAN; Settings writes stay loopback-only; Discord routes are guarded by
  `BACKEND_SHARED_SECRET`; this is not multi-user auth.
- `require_local_settings_write` now documents that its `request.client.host` check
  assumes a direct bind and needs trusted-proxy handling before reverse-proxy use.
- `TODO.md`, `DONE.md`, and README sprint status were updated. Credential rotation
  was intentionally left untouched.

## Verification

- Added backend regression tests for exact-limit and over-limit web/Discord inbox
  capture.
- Per user request, tests were **not run** in this environment.

## Out of scope

- No credential rotation or `.env` edits.
- No multi-user auth, rate limiting, Discord commands/buttons, schema migration,
  model/provider changes, or new dependencies.
