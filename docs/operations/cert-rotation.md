# Cert Rotation

ShoreGuard's **CertRotationService** proactively rotates mTLS client
certs before they expire. It fulfils the M28 observability promise
operationally: the client-cert metadata and `reload_credentials()` hook
have existed since v0.31, but until v0.34.0 no scheduler was wiring them
up.

## What the service does

On startup (when `SHOREGUARD_CERT_ROTATION_ENABLED=true`, the default), a
background task runs every `SHOREGUARD_CERT_ROTATION_POLL_INTERVAL_S`
seconds (default 1h). Each cycle:

1. Lists every registered gateway.
2. For each connected client, reads `cert_info.seconds_until_expiry`.
3. If the remaining validity is below
   `SHOREGUARD_CERT_ROTATION_THRESHOLD_DAYS` (default 7), re-reads the
   credential bytes from the registry and calls `reload_credentials()`
   to rebuild the channel.
4. Records an audit-log entry `gateway.cert_rotated` on success, and a
   webhook `gateway.cert_rotation_failed` when all retries have been
   exhausted.

**The service does not generate new certs.** It assumes an external
process (cert-manager, a cron, an operator running
`shoreguard gateway register --client-cert …`) has landed the fresh
material in the credentials table. ShoreGuard's job is to pick it up.

## Observability

- **Metric:** `sg_gateway_cert_rotations_total{gateway,outcome}` —
  labels `success`, `failure`, `skipped_not_due`, `skipped_no_cert`.
- **Audit:** `gateway.cert_rotated` with
  `{before_seconds_until_expiry, after_seconds_until_expiry, attempts}`.
- **Webhook (on giveup):** `gateway.cert_rotation_failed` payload:

  ```json
  {
    "gateway": "prod-gw-1",
    "reason": "validate_bundle: NotAfter is in the past",
    "retries": 3,
    "seconds_until_expiry": 259200,
    "next_attempt_at": 1714320000.0
  }
  ```

## Runbook — rotation failed

The webhook fires after retries are exhausted in a single poll cycle.
The next cycle starts clean, so if nothing changes the alert will
repeat.

1. **Inspect the `reason`.** Most common cause: the registry still
   holds the expired bytes. Land the fresh cert pair via
   `shoreguard gateway register --client-cert … --client-key …` (or
   your cert-manager equivalent).
2. **Check `sg_gateway_cert_expiry_seconds{gateway}`** to confirm
   whether the new bytes got picked up on the next cycle.
3. **Service disabled?** If an operator set
   `SHOREGUARD_CERT_ROTATION_ENABLED=false`, rotations are paused —
   re-enable after the credentials issue is resolved.

## Multi-replica safety

Rotation is idempotent relative to its inputs (no server-side
mutation), so every replica rotates its own client pool independently.
No advisory lock is needed.
