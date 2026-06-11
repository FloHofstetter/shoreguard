# Live Monitoring

## Real-time log streaming

ShoreGuard streams logs from sandboxes and gateways in real time via WebSocket.
The **Logs** tab in the sandbox detail view shows a live feed that updates as
new entries arrive — no polling required.

## Log sources

Logs are tagged by source so you can tell where each entry originated:

| Source | Description |
|--------|-------------|
| `sandbox` | Output from the sandbox container itself |
| `gateway` | Gateway-level events (health checks, connections) |
| `agent` | Agent activity inside the sandbox |

## Filtering

You can narrow the log stream using several filters:

- **Level** — show only `info`, `warn`, or `error` entries.
- **Source** — limit to a specific source (e.g., `sandbox` only).
- **Since** — show entries after a given timestamp.

Filters are applied server-side so only matching entries are sent over the
WebSocket connection.

## Platform events

In addition to application logs, ShoreGuard surfaces platform events such as:

- Policy decisions (allowed or blocked network requests)
- Sandbox state transitions (creating, running, stopped, error)
- Approval flow activity

## Audit log

For persistent audit logging of all state-changing operations, see the
dedicated [Audit Log](audit.md) guide.

## REST API

Fetch logs for a specific sandbox:

```http
GET /api/gateways/{gw}/sandboxes/{name}/logs?lines=100&since_ms=1700000000000&min_level=warn&sources=sandbox,agent
```

| Parameter | Description |
|-----------|-------------|
| `lines` | Maximum number of log lines to return |
| `since_ms` | Only return entries after this Unix timestamp (milliseconds) |
| `min_level` | Minimum log level (`info`, `warn`, `error`) |
| `sources` | Comma-separated list of sources to include |

## Metrics (Prometheus + Grafana)

ShoreGuard exposes Prometheus metrics at `GET /metrics` — admin-only by
default; set `SHOREGUARD_METRICS_PUBLIC=true` to let a scraper on the
same private network read it without credentials, or scrape with a
service-principal bearer token:

```yaml
# prometheus.yml
scrape_configs:
  - job_name: shoreguard
    metrics_path: /metrics
    static_configs:
      - targets: ["shoreguard:8888"]
    # When metrics are not public:
    authorization:
      type: Bearer
      credentials: <service-principal key>
```

A ready-made dashboard ships in the repository at
[`deploy/grafana/shoreguard.json`](https://github.com/FloHofstetter/shoreguard/blob/main/deploy/grafana/shoreguard.json)
— import it in Grafana (*Dashboards → Import*) and select your
Prometheus datasource. It covers HTTP rate/latency, gateway health,
webhook delivery results, gRPC latency/errors/retries against the
OpenShell gateways, sandbox phase transitions, and client-certificate
expiry.

Key metrics worth alerting on:

| Metric | Meaning |
|--------|---------|
| `shoreguard_gateways_total{status="unreachable"}` | A gateway is down (also fires the `gateway.unreachable` webhook) |
| `shoreguard_webhook_deliveries_total{status="failed"}` | Notifications are not reaching you — alert on `rate() > 0` |
| `sg_grpc_call_total{code!="OK"}` | RPC errors against a gateway |
| `sg_gateway_cert_expiry_seconds` | Client cert expiry; the rotation service should keep this high |
| `shoreguard_http_request_duration_seconds` | UI/API latency histogram |
