# Homelab / single-box operations

ShoreGuard's smallest supported deployment is **one box, one gateway**: a
DGX Spark (or any Linux machine) running an OpenShell gateway with
ShoreGuard alongside it. This page covers installing, backing up, and
monitoring that shape. For the first-run walkthrough see
[Solo Dev](../getting-started/solo-dev.md).

## arm64 / DGX Spark

Every NVIDIA GB10 device — the DGX Spark Founders Edition and all OEM
variants (ASUS Ascent GX10, HP ZGX Nano, Lenovo ThinkStation PGX, …) — is
**aarch64**. ShoreGuard supports this natively:

- The release container images are multi-arch (`linux/amd64` + `linux/arm64`).
- The unit test suite runs on a native arm64 runner in CI on every push,
  not just cross-built.
- ShoreGuard is pure Python; the only binary dependencies (grpcio,
  SQLAlchemy drivers) publish aarch64 wheels.

`pip install shoreguard` on DGX OS therefore works out of the box.

## Option A — Docker Compose (SQLite, one container)

```bash
cd deploy
SHOREGUARD_SECRET_KEY=$(openssl rand -hex 32) \
SHOREGUARD_ADMIN_PASSWORD=pick-a-password \
docker compose -f docker-compose.homelab.yml up -d
```

This runs ShoreGuard with its default SQLite database (WAL mode) inside a
named volume — no PostgreSQL. The port is published on **127.0.0.1 only**;
see [Remote access](#remote-access) below before exposing anything.

SQLite is a supported production shape for a single replica. The startup
config check flags it as a `WARN` (informational); it only becomes an
`ERROR` when `SHOREGUARD_REPLICAS` > 1, because concurrent replicas must
not share a SQLite file.

## Option B — bare metal with systemd

A hardened unit file ships in
[`deploy/systemd/shoreguard.service`](https://github.com/FloHofstetter/shoreguard/blob/main/deploy/systemd/shoreguard.service):

```bash
sudo useradd --system --home-dir /var/lib/shoreguard --shell /usr/sbin/nologin shoreguard
sudo python3 -m venv /opt/shoreguard
sudo /opt/shoreguard/bin/pip install shoreguard
sudo cp deploy/systemd/shoreguard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shoreguard
```

Configuration lives in `/etc/shoreguard/env` (`SHOREGUARD_*` variables);
state — the SQLite DB and the on-disk secret key — lives in
`/var/lib/shoreguard` (the unit sets `XDG_CONFIG_HOME` there).

For `shoreguard --local` (Docker lifecycle management of local gateways)
add `SupplementaryGroups=docker` to the unit.

## Backup & restore

All management-plane state is in one directory (the gateways and sandboxes
themselves live on the OpenShell gateway, not in ShoreGuard):

| Install | State directory |
| --- | --- |
| Docker Compose (homelab) | volume `sgdata` → `/home/shoreguard/.config/shoreguard` |
| systemd | `/var/lib/shoreguard/shoreguard` |
| pip / dev | `~/.config/shoreguard` |

**Backup** (SQLite): either stop the service and copy the directory, or do a
live, consistent snapshot:

```bash
sqlite3 ~/.config/shoreguard/shoreguard.db ".backup /backups/shoreguard-$(date +%F).db"
```

Include the `.secret_key` file from the same directory — without it,
existing sessions and signed tokens are invalidated after a restore.

**Restore:** stop ShoreGuard, put the files back, start it. Migrations run
automatically on startup, so restoring an older backup into a newer
ShoreGuard works.

## Health monitoring

- `GET /healthz` — liveness (the process is up).
- `GET /readyz` — readiness, including the background-task supervisor
  snapshot (gateway health monitor, cleanup, …).
- `GET /metrics` — Prometheus metrics (admin-authenticated by default;
  `SHOREGUARD_AUTH_METRICS_PUBLIC=true` to open it to a local scraper).

A minimal external watchdog is one line of cron on another machine:

```bash
*/5 * * * * curl -fsS --max-time 5 http://spark:8888/healthz > /dev/null || ntfy pub alerts "ShoreGuard down"
```

ShoreGuard itself watches the *gateways* (the `health_monitor` background
task) and fires `gateway.unreachable` / `gateway.recovered` webhook events
on state transitions — point a [webhook](../guides/webhooks.md) at ntfy or
Telegram to get gateway outages on your phone.

## Remote access

Do **not** publish the ShoreGuard port to the internet, and avoid binding
it to the LAN unauthenticated. The recommended remote-access path is
Tailscale:

```bash
tailscale serve --bg 8888
```

ShoreGuard stays bound to loopback; Tailscale terminates TLS and makes the
UI reachable from every device in your tailnet (including your phone)
under the machine's tailnet name. See [Tailscale access](tailscale.md) for
the full recipe, including automatic login via tailnet identity headers
(`SHOREGUARD_TAILSCALE_IDENTITY`) and the topbar QR button for phones.

## Passkeys & push on the phone

Once the UI is reachable over HTTPS (Tailscale gives you that for free),
two things make the phone experience first-class:

- **Passkeys** — on `/profile`, register a passkey; from then on the
  login page offers "Sign in with a passkey" and the phone's screen lock
  signs you in. No password manager gymnastics on mobile. Enabled by
  default (`SHOREGUARD_PASSKEYS_ENABLED`); pin the relying-party ID with
  `SHOREGUARD_PASSKEY_RP_ID` if you serve under multiple names.
- **Web Push** — enable notifications via the QR/phone dialog in the
  top bar, then create a `webpush` webhook choosing which events reach
  the device. No ntfy/Telegram account needed; payloads are end-to-end
  encrypted. See the [webhooks guide](../guides/webhooks.md).
