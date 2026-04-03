# Gateway Management

## What is a gateway?

A **gateway** is an NVIDIA OpenShell instance that runs sandboxes. Each gateway
exposes a gRPC endpoint that ShoreGuard connects to for sandbox management,
policy editing, and log streaming. You can register as many gateways as you
need and manage them all from the ShoreGuard dashboard.

![Gateways](../screenshots/gateways.png)

## Registering a gateway

### Via the Web UI

Open the **Gateways** page and click **+ Register**. Fill in the gateway name,
endpoint URL, authentication mode, and — if using mTLS — upload the
certificates.

### Via the REST API

```http
POST /api/gateway/register
Content-Type: application/json

{
  "name": "production-gw",
  "endpoint": "grpc://gateway.example.com:443",
  "auth_mode": "mtls",
  "ca_cert": "...",
  "client_cert": "...",
  "client_key": "..."
}
```

## Supported authentication modes

| Mode | Description |
|------|-------------|
| `mtls` | Mutual TLS with CA, client certificate, and client key |
| `api_key` | API key passed in gRPC metadata |
| `none` | No authentication — development/testing only |

## Health monitoring

ShoreGuard probes each registered gateway approximately every **30 seconds**.
The dashboard shows the current status and a `last_seen` timestamp so you can
spot connectivity issues at a glance.

## Testing a connection

You can trigger an explicit connection test at any time:

- **Web UI** — click the **Test** button next to the gateway entry.
- **API** — call the gateway test endpoint.

The test performs a full gRPC health check and reports the result immediately.

## Gateway detail

Each gateway has a dedicated detail page at `/gateways/{name}` showing status,
connection info, and management controls.

![Gateway Detail](../screenshots/gateway-detail.png)

### Start, Stop, and Restart

When running in [local mode](../admin/local-mode.md), the gateway detail page
shows **Stop**, **Restart**, and **Test Connection** buttons to manage the
Docker-based gateway lifecycle directly from the browser.

### Inference provider

The **Inference Provider** card lets you configure which LLM provider and model
the gateway uses for agent inference. You can also set a per-route **timeout**
in seconds (0 uses the default of 60s) — useful for large models with long
response times.
