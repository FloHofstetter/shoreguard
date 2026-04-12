# Gateway Discovery

ShoreGuard can auto-register OpenShell gateways announced via DNS
SRV records, so a freshly-rolled microVM host shows up in the
gateway list without any manual step.

## What it solves

In a multi-tenant Kubernetes cluster or a microVM fleet, new
OpenShell gateways get created continuously. Manual registration via
the UI or Terraform is a bottleneck. SRV-based discovery turns the
problem into plain DNS: whoever controls the gateway's namespace
publishes a `_openshell._tcp.<domain>` record, ShoreGuard polls it on
a timer, and any new entry is validated + registered automatically.

## How it works

Discovery runs in two modes:

1. **Manual trigger** — `POST /api/gateway/discover` (operator+),
   used for ad-hoc scans and the **Discover** button on the gateways
   list page.
2. **Background loop** — an asyncio task in the application
   lifespan (analogous to `_health_monitor`). Configurable via
   `SHOREGUARD_DISCOVERY_*` settings, off by default.

Both paths resolve `_openshell._tcp.<domain>` SRV records for every
configured discovery domain. Each SRV target `(host, port)` becomes
a candidate endpoint. Candidates flow through the **same**
`_validate_endpoint_format` guard as manual registration — so the
`*.svc.cluster.local` whitelist still applies and other private IPs
are still rejected unless `local_mode` is enabled.

Candidates that already exist (matched by endpoint) are skipped.
New candidates get a derived name from the SRV target host
(sanitised, max 253 chars), with the port appended when it's not
443 or 30051.

## Configuration

All settings live under `SHOREGUARD_DISCOVERY_*`. Off by default.

| Setting | Default | Description |
|---|---|---|
| `SHOREGUARD_DISCOVERY_ENABLED` | `false` | Enable the background loop |
| `SHOREGUARD_DISCOVERY_DOMAINS` | *(empty)* | Comma-separated domains to scan |
| `SHOREGUARD_DISCOVERY_INTERVAL_SECONDS` | `300` | Background loop interval |
| `SHOREGUARD_DISCOVERY_DEFAULT_SCHEME` | `https` | Scheme for derived endpoints |
| `SHOREGUARD_DISCOVERY_AUTO_REGISTER` | `true` | Actually register, vs. report only |
| `SHOREGUARD_DISCOVERY_RESOLVER_TIMEOUT_SECONDS` | `5` | DNS query timeout |

See [Settings Reference](../reference/settings.md) for the full list.

## Triggering manually

From the UI, click **Discover** on the gateways list page — the
result counts appear in a dismissable banner and the table refreshes.

Via API:

```http
POST /api/gateway/discover
Content-Type: application/json

{"domains": ["svc.cluster.local"]}
```

The optional `domains` array overrides the configured list for this
scan only. Audit-logged as `gateway.discovered`.

Inspect the last scan result without re-triggering:

```
GET /api/gateway/discovery/status
```

## Publishing SRV records

Any DNS provider works. Example BIND zone entry:

```
_openshell._tcp.cluster.local. 300 IN SRV 10 10 30051 gw1.cluster.local.
_openshell._tcp.cluster.local. 300 IN SRV 10 10 30051 gw2.cluster.local.
```

In CoreDNS (typical Kubernetes setup), Services of type
`ClusterIP` with a named port automatically get SRV records
matching `_{port}._{proto}.{svc}.{ns}.svc.cluster.local` — point the
ShoreGuard discovery domain at that suffix and no extra config is
needed.

## Reference

- API: [`POST /api/gateway/discover`](../reference/api.md#discovery-m22-v0302)
- Dependency: `dnspython >= 2.6` (MIT).
- Demo: `scripts/m22_demo.py` phases 5–7.
