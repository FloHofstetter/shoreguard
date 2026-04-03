# Database Migration Runbook

This document covers how to back up the database before running a migration and how to roll back
when things go wrong. Migration 007 (integer PK refactor) gets its own section because it is
irreversible and requires extra caution.

---

## Before Any Migration

**Always back up first.** Alembic does not create snapshots automatically.

### SQLite backup

```bash
# Copy the database file while the application is stopped (safest)
cp /var/lib/shoreguard/shoreguard.db /var/lib/shoreguard/shoreguard.db.bak-$(date +%Y%m%d%H%M%S)

# If the application must keep running, use the SQLite online backup tool
sqlite3 /var/lib/shoreguard/shoreguard.db ".backup '/var/lib/shoreguard/shoreguard.db.bak-$(date +%Y%m%d%H%M%S)'"
```

### PostgreSQL backup

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

This works for migrations that implement a `downgrade()` function. It **does not work** for
migration 007 (see below).

---

## Migration 007 — Schema Cleanup (Irreversible)

Migration 007 (`007_schema_cleanup.py`) makes three destructive changes:

1. Converts timestamp columns from `String` to `DateTime` across all tables.
2. Rebuilds the `gateways` table with an auto-increment integer primary key (replacing the old
   string `name` PK). The old table is dropped.
3. Rewrites foreign key references in `user_gateway_roles`, `sp_gateway_roles`, and `audit_log`
   from `gateway_name` (string) to `gateway_id` (integer FK with `CASCADE`/`SET NULL`).

`downgrade()` raises `NotImplementedError` by design — there is no safe automatic reversal.

### If migration 007 fails mid-run

Stop the application immediately. Do not attempt a partial retry. Restore from backup:

**SQLite:**
```bash
# Stop the application
systemctl stop shoreguard   # or however you manage the process

# Restore backup
cp /var/lib/shoreguard/shoreguard.db.bak-<timestamp> /var/lib/shoreguard/shoreguard.db

# Verify the backup is on revision 006
sqlite3 /var/lib/shoreguard/shoreguard.db "SELECT version_num FROM alembic_version;"
# Expected output: 006
```

**PostgreSQL:**
```bash
# Stop the application
systemctl stop shoreguard

# Drop the damaged database and restore from dump
dropdb -h <host> -U <user> <dbname>
createdb -h <host> -U <user> <dbname>
pg_restore -h <host> -U <user> -d <dbname> shoreguard_pre_migration_<timestamp>.dump

# Verify revision
psql -h <host> -U <user> -d <dbname> -c "SELECT version_num FROM alembic_version;"
# Expected output: 006
```

### If migration 007 applied cleanly but you need to revert

There is no automated path. You must restore from the pre-migration backup. All data written
between the backup and the restore point will be lost. Weigh this against a forward fix before
deciding.

### Checklist before applying migration 007 in production

- [ ] Application stopped or in maintenance mode
- [ ] Database backup completed and stored off-host
- [ ] Backup integrity verified (restore tested in a non-production environment)
- [ ] CI migration test passed on this exact codebase version
- [ ] Rollback plan confirmed with team (restore backup — no automated downgrade)

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
explicit.
