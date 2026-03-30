# User Guide

ShoreGuard provides a web UI and REST API for managing OpenShell gateways,
sandboxes, and security policies. This guide walks through each major feature
area so you can get the most out of your deployment.

---

## Feature overview

### [Gateways](gateways.md)

Register and monitor multiple OpenShell gateways from a single dashboard.
Health probing, connection testing, and multi-gateway selection are all
built in.

### [Sandboxes](sandboxes.md)

Create secure, isolated environments for AI agents using the step-by-step
wizard or the REST API. Manage the full sandbox lifecycle — from creation
through execution to teardown.

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
