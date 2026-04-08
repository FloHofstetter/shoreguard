# Webhooks

Subscribe external services to ShoreGuard events. Whenever a subscribed event
occurs (sandbox created, policy updated, approval decision, etc.), ShoreGuard
sends a notification to your configured URL.

---

## Creating a webhook

### Via the Web UI

Navigate to **Admin > Webhooks** and click **Create Webhook**. Provide a URL,
select the events to subscribe to, and choose a channel type.

### Via the API

```http
POST /api/webhooks
Content-Type: application/json

{
  "url": "https://hooks.slack.com/services/T.../B.../xxx",
  "event_types": ["sandbox.created", "sandbox.deleted"],
  "channel_type": "slack"
}
```

The response includes a `secret` for generic webhooks — store it securely for
signature verification.

---

## Channel types

Each webhook has a `channel_type` that controls payload formatting and delivery:

| Type | Delivery | Payload format |
|------|----------|---------------|
| `generic` (default) | HTTP POST with HMAC-SHA256 signature | JSON envelope `{event, timestamp, data}` |
| `slack` | HTTP POST to Slack incoming webhook URL | Slack Block Kit with mrkdwn and color coding |
| `discord` | HTTP POST to Discord webhook URL | Discord embed with color-coded fields |
| `email` | SMTP delivery | Plain-text email |

### Email channel

For email webhooks, provide SMTP settings in `extra_config`:

```json
{
  "url": "smtp://placeholder",
  "channel_type": "email",
  "event_types": ["*"],
  "extra_config": {
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "smtp_user": "user",
    "smtp_pass": "pass",
    "from_addr": "shoreguard@example.com",
    "to_addrs": ["ops@example.com"]
  }
}
```

---

## Event types

Subscribe to specific events or use `*` for all:

| Event | Trigger |
|-------|---------|
| `sandbox.created` | A new sandbox was created |
| `sandbox.deleted` | A sandbox was deleted |
| `gateway.registered` | A new gateway was registered |
| `gateway.unregistered` | A gateway was removed |
| `inference.updated` | Inference configuration changed |
| `policy.updated` | A sandbox policy was modified |
| `approval.pending` | A new approval request arrived |
| `approval.approved` | An approval was accepted |
| `approval.rejected` | An approval was rejected |
| `webhook.test` | Manual test event |

---

## Signature verification

Generic webhooks include an `X-Shoreguard-Signature` header:

```
X-Shoreguard-Signature: sha256=<hex-digest>
```

Verify by computing `HMAC-SHA256(secret, request_body)` and comparing the hex
digest. Slack and Discord channels do not use signing — they rely on the
provider's built-in URL validation.

### Python example

```python
import hashlib, hmac

def verify(secret: str, body: bytes, signature: str) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

---

## Delivery log and retry

Every delivery attempt is recorded. Query delivery history with:

```http
GET /api/webhooks/{id}/deliveries?limit=50
```

### Retry logic

- **HTTP 5xx and network errors** trigger up to **3 retries** with exponential
  backoff: 5s, 30s, 120s (configurable via `SHOREGUARD_WEBHOOK_RETRY_DELAYS`)
- **HTTP 4xx** errors fail immediately without retry
- Delivery records older than 7 days are purged automatically
  (`SHOREGUARD_WEBHOOK_DELIVERY_MAX_AGE_DAYS`)

---

## Testing

Send a test event to verify your webhook configuration:

```http
POST /api/webhooks/{id}/test
```

This sends a `webhook.test` event with sample data.

---

## Managing webhooks

| Action | Endpoint |
|--------|----------|
| List all | `GET /api/webhooks` |
| Get one | `GET /api/webhooks/{id}` |
| Update | `PUT /api/webhooks/{id}` |
| Delete | `DELETE /api/webhooks/{id}` |

You can temporarily disable a webhook by setting `active: false` via the
update endpoint, without deleting it.

See [Configuration](../reference/configuration.md#webhooks) for all
webhook-related settings.
