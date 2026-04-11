# Rollback Runbook

When a new ShoreGuard deploy is causing problems, this page walks you through
the fastest safe path back to a working state. The flow is linear: follow the
steps in order and stop as soon as the symptom is gone.

Related docs you may need along the way:

- [Troubleshooting](troubleshooting.md) — symptom → cause map for common issues
- [Database Migrations](database-migrations.md) — Alembic upgrade/downgrade details
- [Deployment](deployment.md) — normal upgrade flow

---

## 1. Confirm the symptom

Before rolling back, make sure the new deploy is actually the cause. Check:

```bash
curl -s http://localhost:8888/healthz           # 200 → process is alive
curl -s http://localhost:8888/readyz?verbose=1  # 200 → DB + services healthy
curl -s http://localhost:8888/version           # identity of the running build
```

If `/readyz` reports a dependency failure (DB, gateway, background tasks), the
[troubleshooting guide](troubleshooting.md) is the better starting point —
that's usually a config or infrastructure issue, not a bad image.

Prometheus alerts that fired around the deploy time are the other early
signal. See [Prometheus integration](../integrations/prometheus.md) for the
recommended alerting rules.

---

## 2. Verify the running identity

`GET /version` returns a three-field JSON object that tells you exactly which
artifact is serving traffic:

```json
{
  "version": "0.29.0",
  "git_sha": "a1b2c3d",
  "build_time": "2026-04-10T12:00:00Z"
}
```

If `git_sha` or `build_time` do **not** match what your deploy pipeline was
supposed to publish, the problem is in the pipeline — not in the code. Stop
here and check your Docker Compose env / CI logs first. Rolling back the code
when the pipeline is broken will just reproduce the same mismatch.

---

## 3. Roll back the image

For a Docker Compose stack, switch the image tag back to the previous known-good
release and restart the ShoreGuard container:

```bash
# Option A: semver tag (from the previous release notes)
export SHOREGUARD_VERSION=0.27.0
docker compose pull shoreguard
docker compose up -d shoreguard

# Option B: explicit short-SHA tag (when you don't trust the semver tag)
docker compose pull ghcr.io/flohofstetter/shoreguard:a1b2c3d
docker compose up -d shoreguard
```

The release workflow publishes both `ghcr.io/.../shoreguard:<semver>` and
`ghcr.io/.../shoreguard:<short-sha>` tags, so you always have a
commit-level fallback even if the semver tag was reused.

After the container is up, re-check `/version` to confirm the rollback landed:

```bash
curl -s http://localhost:8888/version
```

---

## 4. Decide whether a DB rollback is also needed

Most releases do not require a database rollback — Alembic migrations are
additive by default, and the previous image can read the newer schema as long
as no columns were dropped. **Stop here** if:

- The current Alembic revision is the same one the old image expects, and
- `/readyz` is back to 200 after the image rollback.

If the bad release introduced a schema migration and the older image refuses
to start because the schema is ahead, you have two options in increasing
severity:

### 4a. Alembic downgrade (reversible migrations)

Follow [Database Migrations — Standard Rollback](database-migrations.md#standard-rollback)
to downgrade one revision. This works for additive migrations and for
migrations whose `downgrade()` function is implemented.

### 4b. Restore from backup (irreversible migrations)

If `alembic downgrade` raises `NotImplementedError`, the migration was marked
as one-way. Restore the pre-migration backup:

```bash
# Assuming you took a backup before the upgrade — see
# database-migrations.md for the "Before Any Migration" checklist.
uv run python -m scripts.restore \
    --source /var/backups/shoreguard/shoreguard-20260410T120000Z.sqlite \
    --url sqlite:////var/lib/shoreguard/shoreguard.db
```

`scripts/restore.py` auto-detects SQLite vs Postgres from the URL scheme and
uses `sqlite3.Connection.backup()` or `pg_restore --clean --if-exists`
respectively.

After the restore, restart ShoreGuard to let it re-read the schema:

```bash
docker compose up -d shoreguard
```

---

## 5. Verify

Run the same three probes you used in step 1:

```bash
curl -s http://localhost:8888/healthz
curl -s http://localhost:8888/readyz?verbose=1
curl -s http://localhost:8888/version
```

`/version` should now show the rolled-back `git_sha` and `build_time`.
`/readyz` should be 200 with every dependency check green. Hit a couple of
authenticated endpoints (list gateways, list sandboxes) as a smoke test.

Clear any alerts that fired during the incident and confirm they stay clear
for one observation window before declaring the rollback successful.

---

## 6. Post-mortem checkpoint

Before moving on:

- File an incident note with the `/version` of the bad build, the symptom,
  the rollback path taken, and the total downtime.
- Add a regression test that would have caught the issue in CI if possible.
- If the rollback required a DB restore, double-check that your backup
  cadence (see [Database Migrations — Before Any Migration](database-migrations.md#before-any-migration))
  is short enough to match your RPO.
- Don't redeploy the same bad image over the rollback. Fix the issue on a
  new commit, let it go through CI, and make sure the new `/version`
  matches before declaring the incident closed.
