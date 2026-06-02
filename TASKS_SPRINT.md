# Sprint 6 — Hardening & Backups

> Goal: the app is reliable enough to trust with real data.

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

- [x] Nightly SQLite backup — `scripts/backup_db.sh` (stdlib `sqlite3.Connection.backup()`
      online snapshot + 14-day prune, cron line in README; no external CLI dependency)
- [x] `activity_events` model + migration — append-only audit log (no `deleted_at`,
      documented exception); migration `09002cc3cb7c`
- [x] `backend/app/services/activity.py` — `record_event`/`list_events`, called from
      `services/projects.py` + `services/tasks.py` (task events guarded on `project_id`)
- [x] `src/features/projects/ActivityFeed.tsx` — per-project feed on the tasks page
      (`GET /api/projects/{id}/activity`, `useProjectActivity` hook, refreshes on task change)
- [x] Expanded eval suite — 20 cases in `extraction_cases.yaml` (was 7)
- [ ] `docker-compose.yml` — backend + frontend in containers (**deferred**: "clean
      restarts, not prod"; not needed to trust the app with data — the one open box)
- [x] README updated: backup script + cron, activity-log schema note, Sprint 6 status
- [x] Full manual smoke test of the entire flow, top to bottom (project/task lifecycle
      verified in the browser; AI inbox→process→accept path verified live against Ollama
      — accepted candidate logs a `created` task event in the feed)

