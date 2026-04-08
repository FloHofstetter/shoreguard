# Prometheus Metrics

ShoreGuard exposes a Prometheus-compatible metrics endpoint at `/metrics`.

---

## Scrape configuration

Add ShoreGuard as a scrape target in your Prometheus configuration:

```yaml
scrape_configs:
  - job_name: shoreguard
    static_configs:
      - targets: ["shoreguard:8888"]
```

## Authentication

By default, the `/metrics` endpoint requires authentication (session cookie or
bearer token). To expose it without authentication — for example, when
Prometheus cannot present credentials — set:

```bash
export SHOREGUARD_METRICS_PUBLIC=true
```

In no-auth mode (`SHOREGUARD_NO_AUTH=1`), the endpoint is always accessible.

---

## Exposed metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `shoreguard_info` | Info | `version` | ShoreGuard build information |
| `shoreguard_gateways_total` | Gauge | `status` | Number of registered gateways by status |
| `shoreguard_operations_total` | Gauge | `status` | Number of tracked long-running operations by status |
| `shoreguard_webhook_deliveries_total` | Counter | `status` | Total webhook delivery attempts by result |
| `shoreguard_http_requests_total` | Counter | `method`, `status` | Total HTTP requests by method and status code |
| `shoreguard_http_request_duration_seconds` | Histogram | `method`, `path_template` | HTTP request latency in seconds |

### Histogram buckets

The `shoreguard_http_request_duration_seconds` histogram uses these buckets
(in seconds): 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0.

---

## Request tracking

Every HTTP request is assigned a unique **request ID** (via the `X-Request-ID`
header). If the incoming request already includes this header, ShoreGuard
honours it; otherwise, a new ID is generated. The ID is returned in the
response header and injected into all log entries for the request.

---

## Example alerting rules

```yaml
groups:
  - name: shoreguard
    rules:
      - alert: ShoreGuardDown
        expr: up{job="shoreguard"} == 0
        for: 2m
        annotations:
          summary: "ShoreGuard is unreachable"

      - alert: GatewayUnhealthy
        expr: shoreguard_gateways_total{status="error"} > 0
        for: 5m
        annotations:
          summary: "One or more gateways are in error state"

      - alert: HighErrorRate
        expr: >
          rate(shoreguard_http_requests_total{status=~"5.."}[5m])
          / rate(shoreguard_http_requests_total[5m]) > 0.05
        for: 5m
        annotations:
          summary: "HTTP 5xx error rate exceeds 5%"

      - alert: WebhookDeliveryFailures
        expr: rate(shoreguard_webhook_deliveries_total{status="error"}[15m]) > 0
        for: 15m
        annotations:
          summary: "Webhook deliveries are failing"
```
