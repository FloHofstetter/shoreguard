# Solo Dev (one local box)

You have **one machine** (a workstation, a DGX Spark, a laptop) running an
OpenShell gateway locally, and you want ShoreGuard in front of it fast. This is
the shortest path — no certificates, no remote gateway, no account setup.

!!! info "This is the fastest way to try ShoreGuard"

    For a team managing **multiple** gateways (dev/staging/prod) with logins,
    RBAC and audit, see [Quick Start](quickstart.md) and
    [Installation](installation.md) instead.

## Prerequisites

- Python **3.14+**
- A **running Docker daemon** (ShoreGuard's local mode drives the OpenShell
  gateway container through Docker — `docker info` should succeed)
- The `openshell` CLI on your `PATH` is optional but recommended: if you have
  already run it, ShoreGuard auto-imports your local gateway on startup.

## 1. Install and start in local mode

```bash
pip install shoreguard
shoreguard --local --no-auth
```

- `--local` enables Docker-based gateway lifecycle management and **auto-detects
  your local gateway** — no endpoint or certificates to enter.
- `--no-auth` skips login entirely. Perfect for a single-user box; drop it when
  you want authentication (see [below](#optional-light-auth-on-a-headless-box)).

If Docker isn't running, ShoreGuard still starts but logs a clear warning at
boot — fix Docker, then reload. The full check is at
`GET /api/gateways/diagnostics`.

The database is **SQLite by default** (created automatically with `0600`
permissions) — there is nothing to provision.

## 2. Open the UI

Go to <http://localhost:8888>. With `--no-auth` you land straight on the
dashboard; your local gateway is already listed (auto-imported / auto-detected).
A plaintext loopback gateway connects **without mTLS** in local mode — the certs
are exactly the ceremony you skip here.

## 3. Create your first sandbox

Open the **Sandbox Wizard**:

1. Pick an agent type.
2. **Container image** — leave it blank to use the gateway's default base image.
3. Providers are **optional** — sandboxes auto-create them on demand, so you can
   launch with none and add API keys later.
4. Launch.

If a sandbox doesn't reach the ready state in time, the warning tells you exactly
what to check (gateway health, Docker, and the `SHOREGUARD_SANDBOX_READY_TIMEOUT`
knob for slow first-time image pulls).

## 4. Open a terminal

Click the sandbox, then **Terminal** for a full in-browser xterm session.

---

## Optional: light auth on a headless box

`--no-auth` is fine for a personal box. If you reach the machine only over SSH
but still want a login, bootstrap the first admin **without** the browser setup
wizard, in either of two ways:

```bash
# Env-var bootstrap: seeds admin@localhost on first start when no users exist
SHOREGUARD_ADMIN_PASSWORD='choose-a-strong-one' shoreguard --local

# …or create a user explicitly from the CLI
shoreguard create-user you@example.com --role admin
```

Then start without `--no-auth` and log in. (With `--no-auth` these are moot —
there is no login.)

## Where to go next

- [Installation](installation.md) — PostgreSQL, source installs, release verification.
- [Quick Start](quickstart.md) — registering **remote** gateways and the
  multi-gateway workflow.
