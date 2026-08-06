# Backups & Litestream

Two complementary layers protect `data/app.db`:

1. **Snapshots** — `./scripts/backup_db.sh` takes a consistent online snapshot
   into `data/backups/` (prunes past `BACKUP_RETENTION_DAYS`, default 14; it
   must be a non-negative integer — anything else aborts before any snapshot is
   written or pruned). Run it from cron on the host, or from inside the
   container. Each snapshot is written under a temporary name and renamed into
   place only once complete, so **every `app-*.db` in `data/backups/` is a whole
   snapshot** — safe to restore by picking the newest. An interrupted run leaves
   at most an `app-*.db.tmp.<pid>` file, which later runs sweep once it ages
   past the retention window.
2. **Continuous replication (Litestream)** — streams the write-ahead log to a
   replica as writes land, giving point-in-time recovery *between* snapshots.
   Litestream is not a snapshot archive — **keep running both**.

## Replica targets

By default the `litestream` compose service writes a local file replica to
`data/replica/` (zero config, no credentials). That protects against app-level
corruption, a bad migration, or a mistaken delete, but **not disk loss** — the
replica shares the mount. For off-host durability, repoint the replica `path`
in `litestream.yml` at an NFS / second-disk mount, or uncomment the S3 block
there and set `LITESTREAM_S3_*` in `.env` — no cloud dependency is pulled in by
default.

## Restore

Restore runs against the same config. It reconstructs the DB from the replica's
snapshot + WAL into a scratch file you can inspect before going live:

```
docker compose run --rm --no-deps litestream \
  restore -config /etc/litestream.yml -o /data/restored.db /data/app.db
# Go live: stop the app, replace data/app.db with the restored copy, restart.
```

*Restore drill verified 2026-07-03:* with the stack up, a project created
through the API **after** the initial snapshot was present in a
`litestream restore` of the file replica, and every table's row count matched
the live DB — confirming the WAL stream (not just the startup snapshot)
round-trips.

## Non-docker (`main.sh`) setup

Litestream also runs as a plain host binary against the same `litestream.yml`
(point `path` at your real `data/app.db`). Run it under systemd so it restarts
with the box:

```
# /etc/systemd/system/litestream.service — then: systemctl enable --now litestream
[Service]
ExecStart=/usr/local/bin/litestream replicate -config /path/to/litestream.yml
Restart=always
```
