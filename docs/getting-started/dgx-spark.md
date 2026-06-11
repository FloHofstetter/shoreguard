# DGX Spark quickstart

From factory-fresh Spark to a managed, phone-reachable agent box in
about ten minutes. This guide assumes the June-2026 DGX OS
out-of-box experience, which sets up **NemoClaw → OpenShell → a
sandboxed agent** on first boot — ShoreGuard adopts exactly that
gateway. Everything here is aarch64-native (see
[Homelab / Single Box](../operations/homelab.md#arm64--dgx-spark)).

## 1. Install ShoreGuard on the Spark

```bash
pip install shoreguard
shoreguard --local --single-user
# prompts once for your admin password (account: admin@localhost)
```

Open `http://localhost:8888` — you are logged in as admin.
`--single-user` is the homelab middle ground: real authentication, one
account, password rotatable via `SHOREGUARD_ADMIN_PASSWORD`. (Plain
`--local --no-auth` also works while everything stays on loopback.)

## 2. Adopt the gateway

If NemoClaw (or `openshell init`) already created a gateway on this
machine, local mode imported it at startup. If not — or after adding
one later — click **Gateways → Scan this machine**. The scan reads
`~/.config/openshell/gateways/*/metadata.json` (including
NemoClaw-provisioned gateways, mTLS bundle and all) and shows a
per-entry log of anything it skipped.

Other Sparks on your LAN can be found via discovery:

```bash
SHOREGUARD_DISCOVERY_MDNS_ENABLED=true shoreguard --local --single-user
```

then **Gateways → Discover**. mDNS finds every gateway that announces
`_openshell._tcp` on the local network. A gateway box can announce
itself with one avahi file —
`/etc/avahi/services/openshell.service`:

```xml
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">OpenShell on %h</name>
  <service>
    <type>_openshell._tcp</type>
    <port>30051</port>
  </service>
</service-group>
```

(DNS-SRV discovery for routed networks works too — see
[Gateway Discovery](../guides/gateway-discovery.md).)

## 3. Reach it from your phone

```bash
tailscale serve --bg 8888
```

ShoreGuard stays on loopback; your tailnet gets
`https://spark.<tailnet>.ts.net`. Use the topbar **QR button** to open
it on your phone, and optionally log in automatically with your
tailnet identity (`SHOREGUARD_TAILSCALE_IDENTITY=true`) — full recipe
in [Tailscale access](../operations/tailscale.md).

## 4. Get pushed, not polled

Create an [ntfy webhook](../guides/webhooks.md#ntfy-channel-push-notifications)
subscribed to `approval.pending`, `approval.escalated`,
`gateway.unreachable`, and `digest.daily`, then:

```bash
SHOREGUARD_PUBLIC_URL=https://spark.<tailnet>.ts.net
SHOREGUARD_WEBHOOK_ONE_TAP_APPROVALS=true   # approve/reject buttons on the push
SHOREGUARD_DIGEST_ENABLED=true              # morning report at 7:00 local
```

Overnight agent runs now buzz your phone when they need a decision,
the gateway watchdog pings you if the box stops answering, and the
daily digest summarises what happened while you slept.

## 5. Check your exposure

Open **Admin → Security Check**. It verifies bind address vs. auth
mode, per-gateway mTLS, open registration, and flags anything that
looks like the exposed-agent-gateway misconfigurations of early 2026
— each finding with a fix hint.

## Where to go next

- [Solo Dev](solo-dev.md) — sandboxes, local model servers, terminals.
- [Homelab / Single Box](../operations/homelab.md) — systemd install,
  backup/restore, health monitoring, the SQLite compose.
- When the one Spark becomes a team of Sparks: register the others
  (Discover / mDNS), then [RBAC](../admin/rbac.md), approval quorums,
  and gateway-scoped roles are already there.
