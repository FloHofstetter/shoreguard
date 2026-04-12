# Bypass Detection

ShoreGuard's Bypass Detection dashboard surfaces OCSF events that look
like attempts to route around an active policy — useful both during
incident response and as a leading indicator that a policy is too
permissive on paper and too strict in practice.

## What it solves

Agents rarely fail loudly. A denial followed by a silent retry on a
different port, a DNS query that carries data in the subdomain label,
or egress on a port nobody allow-listed are all signals that the
agent (or something compromising it) is probing the edges of the
sandbox. Unit tests do not see these patterns — they only appear at
runtime, across many events.

## How it works

The `BypassService` subscribes to the OCSF event stream and runs three
classifiers:

- **Denial → success** — an allowed event whose tuple
  `(binary, destination, port)` matches a recent denial.
- **Unusual egress port** — traffic on a port outside the sandbox's
  declared allow-list, scored by deviation from the baseline.
- **DNS exfiltration signatures** — query patterns with
  high-entropy subdomain labels or oversized TXT responses.

Each hit is written into an in-memory ring buffer (last 1 000 events
per gateway) with a severity (`critical` / `high` / `medium` / `low`)
and a MITRE ATT&CK technique mapping. The service is purely
observational — it does *not* block the flow, only record it.

## Using the dashboard

Open the **Bypass** tab on the gateway detail page. The top strip
shows per-severity counts over the visible window; the filter chips
narrow the event list; each row links through to the raw OCSF event
and its ATT&CK mapping.

For SIEM correlation, scrape the events programmatically:

```bash
curl -sH "Authorization: Bearer $SHOREGUARD_TOKEN" \
  "$SHOREGUARD_URL/api/gateways/dev/bypass?severity=high&limit=100"
```

## Reference

- API: [`GET /api/gateways/{gw}/bypass`](../reference/api.md#bypass-detection-m15-v0302)
- Summary: `GET /api/gateways/{gw}/bypass/summary`

## Limits

The ring buffer is in-memory — restarting ShoreGuard clears the
history. Persist bypass events to your SIEM if you need long-term
retention.
