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
