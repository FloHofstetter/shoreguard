#!/usr/bin/env bash
# Sandbox init wrapper — replaces "sleep infinity" with OpenClaw gateway.
#
# OpenShell sets OPENSHELL_SANDBOX_COMMAND="sleep infinity" for all
# sandboxes created via the gRPC API. This wrapper intercepts that
# and starts the OpenClaw gateway instead.
#
# Placed at /usr/local/bin/sleep (shadows /usr/bin/sleep) so that
# "sleep infinity" invoked by openshell-sandbox runs this script.

set -euo pipefail

# Only intercept "sleep infinity" — pass through all other sleep calls
if [ "${1:-}" != "infinity" ]; then
    exec /usr/bin/sleep "$@"
fi

echo "[shoreguard] Intercepting 'sleep infinity' — starting OpenClaw gateway..."

export HOME=/sandbox
export OPENCLAW_STATE_DIR=/sandbox/.openclaw

# Wait for config to be injected by ShoreGuard
echo "[shoreguard] Waiting for openclaw.json..."
TIMEOUT=300
ELAPSED=0
while [ ! -f /sandbox/.openclaw/openclaw.json ]; do
    if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
        echo "[shoreguard] FATAL: openclaw.json not found after ${TIMEOUT}s" >&2
        exec /usr/bin/sleep infinity
    fi
    sleep 2
    ELAPSED=$((ELAPSED + 2))
done
echo "[shoreguard] Config found."

# Start the gateway (replaces this process)
exec openclaw gateway run --bind lan --port 18789 --allow-unconfigured
