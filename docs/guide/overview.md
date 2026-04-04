# User Guide

ShoreGuard provides a web UI and REST API for managing OpenShell gateways,
sandboxes, and security policies. This guide walks through each major feature
area so you can get the most out of your deployment.

---

## Feature overview

### [Gateways](gateways.md)

Register and monitor multiple OpenShell gateways from a single dashboard.
Health probing, connection testing, and gateway lifecycle management are all
built in. Organise your fleet with **descriptions** and **labels** (key=value
pairs) and filter by label via the API.

### [Sandboxes](sandboxes.md)

Create secure, isolated environments for AI agents using the step-by-step
wizard or the REST API. Pre-configured **sandbox templates** (data-science,
web-dev, secure-coding) bundle image, GPU, providers, and policy presets for
one-click creation. Manage the full sandbox lifecycle — from creation through
execution to teardown.

### [Security Policies](policies.md)

Edit network rules, filesystem paths, and process settings with a visual
editor. Apply one-click presets instead of hand-editing YAML.

### [Approvals](approvals.md)

Review agent access requests in real time. When an agent hits a blocked
endpoint, ShoreGuard surfaces the draft policy recommendation so you can
approve, reject, or edit individual rules.

### [Live Monitoring](monitoring.md)

Stream logs from sandboxes, gateways, and agent activity in real time via
WebSocket. Filter by level, source, or timestamp.

### Webhooks & Notifications

Subscribe external services to ShoreGuard events (sandbox lifecycle, gateway
registration, approval decisions, policy and inference changes). Webhooks
support multiple channel types:

- **Generic** — HMAC-SHA256 signed HTTP POST for custom integrations
- **Slack** — Block Kit formatted messages with color coding
- **Discord** — Embed messages with color-coded fields
- **Email** — SMTP delivery for ops team alerts

Every delivery is tracked in a **delivery log** with automatic **retry**
(3 attempts with exponential backoff) for transient failures. See the
[API reference](../reference/api.md#webhooks) for endpoint details.

### API Key Management

**Service principals** provide programmatic access for CI/CD pipelines and
Terraform. Keys use an `sg_` prefix for identification and support optional
**expiry** dates and **rotation** without downtime. See the
[service principals guide](../admin/service-principals.md) for details.

### Prometheus Metrics

ShoreGuard exposes a `/metrics` endpoint in Prometheus text format
(unauthenticated, like the health probes). Tracks gateway status, operations,
webhook deliveries, and HTTP request counts. See the
[API reference](../reference/api.md#metrics) for the full metric list.
