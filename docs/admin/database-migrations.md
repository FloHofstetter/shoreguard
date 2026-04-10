# Database Migrations

This document covers how to back up the database before running a migration and how to roll back
when things go wrong.

For the full incident-response flow (image rollback + DB restore), see the [Rollback
Runbook](rollback.md).

---

## Before Any Migration

**Always back up first.** Alembic does not create snapshots automatically.

### Recommended: bundled backup script

`scripts/backup.py` auto-detects SQLite vs PostgreSQL from the database URL and uses the right
tool (`sqlite3.Connection.backup()` for SQLite online snapshots, `pg_dump --format=custom` for
Postgres). The output filename carries a UTC timestamp and is written to the target directory.

```bash
# Uses SHOREGUARD_DATABASE_URL (or the shoreguard config default) when --url is omitted
uv run python -m scripts.backup --target /var/backups/shoreguard

# Explicit URL override
uv run python -m scripts.backup \
    --url "$DATABASE_URL" \
    --target /var/backups/shoreguard
```

The script prints the destination file path on stdout so you can pipe it into a cron log or an
incident notebook.

Pair this with `scripts/restore.py` — same URL auto-detection, same formats — for the round-trip:

```bash
uv run python -m scripts.restore \
    --source /var/backups/shoreguard/shoreguard-20260410T120000Z.sqlite \
    --url "$DATABASE_URL"
```

### Low-level equivalents

If you cannot or do not want to use the bundled scripts, the underlying commands are:

**SQLite:**

```bash
# Copy the database file while the application is stopped (safest)
cp /var/lib/shoreguard/shoreguard.db /var/lib/shoreguard/shoreguard.db.bak-$(date +%Y%m%d%H%M%S)

# If the application must keep running, use the SQLite online backup tool
sqlite3 /var/lib/shoreguard/shoreguard.db ".backup '/var/lib/shoreguard/shoreguard.db.bak-$(date +%Y%m%d%H%M%S)'"
```

**PostgreSQL:**

```bash
# Full logical dump (recommended before any schema change)
pg_dump -Fc -h <host> -U <user> -d <dbname> \
  -f shoreguard_pre_migration_$(date +%Y%m%d%H%M%S).dump

# Quick schema-only sanity check
pg_dump -s -h <host> -U <user> -d <dbname> -f schema_before.sql
```

Store the backup in a location separate from the database host before proceeding.

---

## Running Migrations

ShoreGuard uses Alembic with embedded migrations (no top-level `alembic.ini`). The application
runs `alembic upgrade head` automatically on startup via `init_db()`. To run manually:

```bash
# Upgrade to latest
uv run python -c "
from shoreguard.db import init_db
init_db('sqlite:////var/lib/shoreguard/shoreguard.db')
"

# Or invoke Alembic directly (SQLite example)
uv run alembic --config /dev/stdin upgrade head <<EOF
[alembic]
script_location = shoreguard/alembic
sqlalchemy.url = sqlite:////var/lib/shoreguard/shoreguard.db
EOF
```

Check the current revision before and after:

```bash
uv run alembic --config /dev/stdin current <<EOF
[alembic]
script_location = shoreguard/alembic
sqlalchemy.url = sqlite:////var/lib/shoreguard/shoreguard.db
EOF
```

---

## Standard Rollback

To roll back the most recent migration:

```bash
uv run alembic --config /dev/stdin downgrade -1 <<EOF
[alembic]
script_location = shoreguard/alembic
sqlalchemy.url = sqlite:////var/lib/shoreguard/shoreguard.db
EOF
```

This works for all current migrations. For destructive migrations that raise
`NotImplementedError` on downgrade, restore from backup instead.

---

## Full Reset

To start from scratch, delete the database and let the application recreate it on startup:

```bash
# SQLite
rm ~/.config/shoreguard/shoreguard.db
uv run shoreguard  # recreates the DB with the latest schema

# PostgreSQL
dropdb -h <host> -U <user> <dbname>
createdb -h <host> -U <user> <dbname>
uv run shoreguard --database-url postgresql://<user>:<pass>@<host>/<dbname>
```

---

## Verifying migrations locally

Use the helper script to test migrations against a fresh database before deploying:

```bash
./scripts/verify_migrations.sh                          # SQLite
DATABASE_URL=postgresql://user:pass@host/db ./scripts/verify_migrations.sh   # PostgreSQL
```

The CI workflow in `.github/workflows/test-migrations.yml` runs this automatically on every PR
that touches migration files.

---

## Warning: migrations that drop columns or change PKs

Any migration that drops a column or changes a primary key type **cannot be automatically rolled
back**. These migrations must be treated as one-way operations:

- Take a backup **before** running the migration.
- Do not rely on `alembic downgrade` for these.
- Document manual steps here if you add a new irreversible migration.

Add a comment in the migration file and raise `NotImplementedError` in `downgrade()` to make this
explicit. For the full recovery path when an irreversible migration goes wrong, see the
[Rollback Runbook](rollback.md).
