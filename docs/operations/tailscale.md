# Tailscale access

Tailscale is the recommended way to reach a homelab ShoreGuard from
anywhere — including your phone — without exposing a port to the LAN or
the internet. ShoreGuard stays bound to loopback; Tailscale handles
transport, TLS, and identity.

## Serve the UI over your tailnet

On the box running ShoreGuard:

```bash
tailscale serve --bg 8888
```

That's it. The UI is now available at `https://<machine>.<tailnet>.ts.net`
from every device in your tailnet, with TLS terminated by Tailscale.
ShoreGuard keeps its loopback bind (`--host 127.0.0.1`, the default in the
systemd unit and the homelab compose), so nothing else can reach it.

Use the **QR button** in the ShoreGuard topbar ("Open on phone") to get the
current page onto your phone.

!!! danger "Do not use Funnel"
    `tailscale funnel` publishes the port to the **public internet**. A
    control plane that manages provider API keys and agent sandboxes has no
    business being internet-facing — stick to `serve` (tailnet-only).

## Log in automatically with your tailnet identity

`tailscale serve` injects identity headers (`Tailscale-User-Login`) into
proxied requests. ShoreGuard can use them as authentication:

```bash
SHOREGUARD_TAILSCALE_IDENTITY=true shoreguard --host 127.0.0.1
```

Rules:

- The header is only honoured for connections **from loopback** — i.e. the
  local `tailscaled` proxy. From any other source it is ignored, so a LAN
  peer cannot forge it even if ShoreGuard is misconfigured onto `0.0.0.0`.
- The Tailscale login must **match an existing user's email** (e.g. create
  the user with `shoreguard create-user you@example.com --role admin`).
  Unknown logins stay unauthenticated — tailnet membership alone grants
  nothing.
- Sessions, service principals, and the regular login keep working
  unchanged; the header is just a third credential source.

With this enabled, opening the tailnet URL from any of your devices logs
you straight in — no password prompt on your phone.

## systemd / Docker notes

- **systemd**: the shipped unit binds loopback already; just run
  `tailscale serve --bg 8888` once (it persists across reboots).
- **Docker (homelab compose)**: the published port binds to `127.0.0.1`
  on the host, which is exactly what `tailscale serve` (running on the
  host) needs.

## See also

- [Homelab / Single Box](homelab.md) — install, backup, monitoring.
- [Security Check](../concepts/security.md#security-check-posture-self-audit)
  — the posture page detects a running Tailscale daemon and flags risky
  exposure alternatives.
