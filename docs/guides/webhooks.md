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
| `ntfy` | HTTP POST to an [ntfy](https://ntfy.sh) server | ntfy JSON publish with title, priority, and tags |
| `telegram` | HTTP POST to the Telegram Bot API | `sendMessage` with HTML text and inline buttons |

### ntfy channel (push notifications)

Point the webhook URL at the **topic URL you subscribe to** on your phone —
either ntfy.sh or a self-hosted server:

```json
{
  "url": "https://ntfy.sh/my-shoreguard-topic",
  "channel_type": "ntfy",
  "event_types": ["approval.pending", "approval.escalated"],
  "extra_config": {"token": "tk_..."}
}
```

`extra_config.token` is optional and sent as a `Bearer` token for servers with
access control. Approval events arrive as high-priority pushes
(`approval.pending` = high, `approval.escalated` = urgent), so an overnight
agent run can ping your phone the moment it needs a human decision.

A self-hosted ntfy on a LAN address is blocked by SSRF protection by default —
exempt it via `SHOREGUARD_SSRF_ALLOWED_IPS` (see
[SSRF protection](../concepts/security.md#ssrf-protection)).

### Telegram channel

Create a bot via [@BotFather](https://t.me/BotFather), find your chat id
(e.g. via @userinfobot), and register the `sendMessage` endpoint including
the chat id as a query parameter:

```json
{
  "url": "https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<CHAT_ID>",
  "channel_type": "telegram",
  "event_types": ["approval.pending", "approval.escalated", "gateway.unreachable"]
}
```

Messages arrive as HTML-formatted Telegram messages; approval events carry
inline **Approve ✓ / Reject ✗** buttons when one-tap links are enabled.

### One-tap approve/reject from your phone

With these two settings, `approval.pending` / `approval.escalated`
notifications carry **signed action links** that cast the vote from a
minimal mobile confirmation page — no login round-trip:

```bash
SHOREGUARD_PUBLIC_URL=https://spark.tail1234.ts.net   # your reachable base URL
SHOREGUARD_WEBHOOK_ONE_TAP_APPROVALS=true
```

On ntfy they appear as notification action buttons, on Telegram as inline
keyboard buttons. Links are HMAC-signed, encode exactly one
`(gateway, sandbox, chunk, decision)` vote, and expire after
`SHOREGUARD_WEBHOOK_ONE_TAP_TTL` seconds (default 1 hour).

!!! warning "Capability semantics"
    Anyone holding such a link can cast that one vote until it expires —
    the notification channel becomes part of the trust boundary. Use
    private channels (your own ntfy topic with an access token, a direct
    Telegram chat), keep the TTL short, and leave the feature off if the
    channel is shared. Votes cast this way are audit-logged with actor
    `one-tap-link`.

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
| `gateway.unreachable` | A previously healthy gateway stopped answering health probes |
| `gateway.recovered` | An unreachable gateway is healthy again |
| `kill_switch.engaged` | The provider kill switch was engaged on a gateway |
| `kill_switch.released` | The kill switch was released (providers re-attached) |
| `inference.updated` | Inference configuration changed |
| `policy.updated` | A sandbox policy was modified |
| `approval.pending` | A new approval request arrived |
| `approval.approved` | An approval was accepted |
| `approval.rejected` | An approval was rejected |
| `digest.daily` | Daily activity digest (when `SHOREGUARD_DIGEST_ENABLED=true`) |
| `webhook.test` | Manual test event |

---

## Signature verification

Generic webhooks include an `X-Shoreguard-Signature` header:

```
X-Shoreguard-Signature: sha256=<hex-digest>
```

Verify by computing `HMAC-SHA256(secret, request_body)` and comparing the hex
digest. Slack, Discord, and ntfy channels do not use signing — they rely on
the provider's built-in URL validation (or, for ntfy, the optional access
token).

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
