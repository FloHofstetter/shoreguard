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
| `mqtt` | One-shot MQTT publish to a broker | JSON envelope `{event, timestamp, data}` on topic `<base>/<event>` |

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

Instead of repeating the relay per webhook, configure it once server-wide
and only pass `to_addrs` per webhook — per-webhook values still override:

```bash
SHOREGUARD_SMTP_HOST=smtp.example.com
SHOREGUARD_SMTP_PORT=587            # default
SHOREGUARD_SMTP_USERNAME=user       # optional
SHOREGUARD_SMTP_PASSWORD=pass       # optional
SHOREGUARD_SMTP_FROM_ADDR=shoreguard@example.com
```

```json
{
  "url": "smtp://placeholder",
  "channel_type": "email",
  "event_types": ["digest.daily"],
  "extra_config": { "to_addrs": ["ops@example.com"] }
}
```

This pairs well with the daily digest (`SHOREGUARD_DIGEST_ENABLED`) — a
`digest.daily` email at 07:00 is the classic homelab morning report.

### MQTT channel (Home Assistant bridge)

Point the webhook URL at your broker (`mqtt://` or `mqtts://` for TLS).
Every subscribed event publishes the generic JSON envelope to
`<base-topic>/<event-type>` — e.g. `shoreguard/kill_switch.engaged` —
so consumers subscribe per event:

```json
{
  "url": "mqtt://192.168.1.10:1883",
  "channel_type": "mqtt",
  "event_types": ["gateway.unreachable", "gateway.recovered",
                  "kill_switch.engaged", "kill_switch.released",
                  "budget.exceeded", "approval.pending"],
  "extra_config": {
    "topic": "shoreguard",
    "username": "shoreguard",
    "password": "secret",
    "qos": 1
  }
}
```

Private broker addresses are allowed in `--local` mode (the homelab
default — your Mosquitto/Home Assistant broker lives on the LAN);
outside local mode, exempt the broker via `SHOREGUARD_SSRF_ALLOWED_IPS`.
Publishing is one-shot and write-only; nothing is read back.

**Home Assistant examples.** A binary sensor that mirrors gateway health:

```yaml
mqtt:
  binary_sensor:
    - name: "OpenShell gateway"
      state_topic: "shoreguard/gateway.unreachable"
      value_template: "OFF"   # any message on this topic means down
      off_delay: 0
```

More idiomatic is an automation pair — actionable phone notification on
a pending approval, with the one-tap links from the payload:

```yaml
automation:
  - alias: "Agent approval needed"
    trigger:
      - platform: mqtt
        topic: "shoreguard/approval.pending"
    action:
      - service: notify.mobile_app_phone
        data:
          title: "Agent approval needed"
          message: "{{ trigger.payload_json.data.sandbox }} requests a rule"
          data:
            actions:
              - action: "URI"
                title: "Approve"
                uri: "{{ trigger.payload_json.data.approve_url }}"
              - action: "URI"
                title: "Reject"
                uri: "{{ trigger.payload_json.data.reject_url }}"
  - alias: "Kill switch engaged — flash the office light"
    trigger:
      - platform: mqtt
        topic: "shoreguard/kill_switch.engaged"
    action:
      - service: light.turn_on
        data: { entity_id: light.office, color_name: red }
```

The `approve_url`/`reject_url` fields are present when
[one-tap approvals](#one-tap-approvereject-from-your-phone) are enabled.

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
