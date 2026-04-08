# Troubleshooting

Common issues and solutions for ShoreGuard deployments.

---

## Startup and connectivity

### Port already in use

```
Error: bind: address already in use
```

Another process is using port 8888. Either stop it or change the port:

```bash
SHOREGUARD_PORT=9999 docker compose up -d
```

### Database connection refused

```
sqlalchemy.exc.OperationalError: connection refused
```

The PostgreSQL container may not be ready yet. Check its health:

```bash
docker compose ps
docker compose logs db
```

Wait for the `db` service to show `healthy` status.

### Docker socket permission denied

```
permission denied while trying to connect to the Docker daemon socket
```

Add your user to the `docker` group:

```bash
sudo usermod -aG docker $USER
# Log out and back in for the change to take effect
```

### Healthcheck failing

```bash
# Check ShoreGuard logs
docker compose logs shoreguard

# Test readiness manually
curl -s http://localhost:8888/readyz | python3 -m json.tool
```

Common causes: database not reachable, missing `SHOREGUARD_SECRET_KEY`,
or the application failed to start (check logs for Python tracebacks).

---

## Authentication

### Sessions invalidated after restart

If `SHOREGUARD_SECRET_KEY` is not set, a random key is generated on each
restart, invalidating all existing sessions. Set a stable secret:

```bash
export SHOREGUARD_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
```

### Account locked out

After too many failed login attempts, accounts are locked for 15 minutes
(configurable via `SHOREGUARD_ACCOUNT_LOCKOUT_DURATION`). Wait for the lockout
to expire or restart the server to clear in-memory rate limits.

---

## OIDC / SSO

### "Authentication failed" after redirect

- **Wrong callback URL** — the redirect URI registered with your provider must
  match exactly: `https://<domain>/api/auth/oidc/callback`
- **Expired state cookie** — the OIDC flow must complete within 5 minutes
  (configurable via `SHOREGUARD_OIDC_STATE_MAX_AGE`). Check clock sync.
- **Missing scopes** — ensure `openid` and `email` are allowed by the provider.

### "Login was denied by the identity provider"

The user denied consent, or the provider rejected the request. Check the
provider's admin console for error details.

### User created with wrong role

Check the `role_mapping` in `SHOREGUARD_OIDC_PROVIDERS_JSON`. Set
`SHOREGUARD_LOG_LEVEL=debug` to see which claims were received.

---

## Webhooks

### Webhook deliveries failing

Check the delivery log:

```http
GET /api/webhooks/{id}/deliveries?limit=10
```

Common causes:

- **Timeout** — the target server takes longer than 10 seconds
  (`SHOREGUARD_WEBHOOK_DELIVERY_TIMEOUT`)
- **SSL errors** — the target uses a self-signed certificate
- **HTTP 4xx** — check the target URL and authentication

### Retries not happening

Only HTTP 5xx and network errors trigger retries. HTTP 4xx errors fail
immediately without retry by design.

---

## WebSocket

### Log stream disconnects

WebSocket connections send a heartbeat ping every 15 seconds
(`SHOREGUARD_WS_HEARTBEAT_INTERVAL`). If disconnections are frequent:

- Check reverse proxy timeout settings — ensure it supports long-lived
  WebSocket connections
- Verify the nginx config includes the `Upgrade` and `Connection` headers
  (see [Deployment](deployment.md#reverse-proxy))

---

## Database

### SQLite "database is locked"

SQLite does not support concurrent writes well. If you see lock errors:

- Ensure only one ShoreGuard instance is running against the same SQLite file
- For multi-instance deployments, switch to PostgreSQL
  ([Configuration](../reference/configuration.md#database))

### Migration errors

See the [Database Migrations](database-migrations.md) runbook for backup and
rollback procedures.

---

## Gateway connections

### "Gateway unreachable" errors

- Verify the gateway endpoint is correct and the gateway is running
- Check that the gRPC port is accessible from the ShoreGuard host
- For mTLS gateways, verify certificates are valid and not expired
- Check `SHOREGUARD_GATEWAY_GRPC_TIMEOUT` if operations are timing out
  (default: 30 seconds)

### Connection drops and reconnects

ShoreGuard uses exponential backoff for gateway reconnections (5s → 60s).
Check the logs for repeated connection failures and verify network stability.
