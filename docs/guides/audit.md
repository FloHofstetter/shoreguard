# Audit Log

ShoreGuard maintains a persistent audit log of all state-changing operations.
Every sandbox creation, policy update, gateway action, approval decision, and
user management event is recorded.

---

## What gets audited

Every action that modifies state is logged, including:

- Sandbox lifecycle (create, delete)
- Policy changes (update, preset application)
- Gateway management (register, remove, start, stop)
- Approval decisions (approve, reject, edit, clear)
- User management (create, delete, role change, invite)
- Service principal management (create, delete, rotate)
- OIDC account linking and creation
- Webhook configuration changes
- Inference configuration changes

---

## Audit entry fields

Each entry records:

| Field | Description |
|-------|-------------|
| `timestamp` | When the action occurred (timezone-aware) |
| `actor` | Email or service principal name |
| `actor_role` | Effective role at time of action |
| `action` | Machine-readable identifier (e.g. `sandbox.create`, `policy.update`) |
| `resource_type` | Type of affected resource (e.g. `sandbox`, `gateway`) |
| `resource_id` | Identifier of the affected resource |
| `gateway_name` | Human-readable gateway name (if applicable) |
| `detail` | Optional free-text detail or JSON payload |
| `client_ip` | IP address of the requesting client |

---

## Viewing the audit log

### Web UI

The **Audit Log** page (under Admin) shows a filterable, paginated table of
all recorded events. Use the filter controls to narrow by actor, resource type,
or action.

![Audit Log](../screenshots/audit-log.png)

### REST API

```http
GET /api/audit?resource=gateway&action=gateway.start&limit=50
```

| Parameter | Description |
|-----------|-------------|
| `actor` | Filter by actor email or name |
| `resource` | Filter by resource type |
| `action` | Filter by action identifier |
| `limit` | Maximum number of entries to return |

---

## Export

Export the audit log as **CSV** or **JSON** for external analysis, compliance
reporting, or SIEM integration:

```http
GET /api/audit/export?format=csv
GET /api/audit/export?format=json
```

The maximum number of rows per export is controlled by
`SHOREGUARD_AUDIT_EXPORT_LIMIT` (default: 10 000).

---

## Retention

Audit entries are retained for **90 days** by default. Configure the retention
period via `SHOREGUARD_AUDIT_RETENTION_DAYS`. Expired entries are purged
automatically by a background cleanup task.

See [Configuration](../reference/configuration.md#audit) for all audit-related
settings.
