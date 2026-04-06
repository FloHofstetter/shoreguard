#!/usr/bin/env bash
# ShoreGuard OpenClaw sandbox entrypoint — hardened startup script.
#
# Responsibilities:
#   1. Verify config integrity (SHA256 hash of openclaw.json)
#   2. Validate symlink targets (.openclaw → .openclaw-data)
#   3. Harden symlinks (chattr +i, best-effort)
#   4. Drop unnecessary capabilities
#   5. Lock down PATH and process limits
#   6. Start gateway as 'gateway' user (privilege separation via gosu)
#
# If running as non-root (OpenShell no-new-privileges), skips gosu and
# runs everything as the current user.

set -euo pipefail

OPENCLAW_HOME="/sandbox/.openclaw"
OPENCLAW_DATA="/sandbox/.openclaw-data"
CONFIG_FILE="${OPENCLAW_HOME}/openclaw.json"
HASH_FILE="${OPENCLAW_HOME}/.config-hash"
LOG_DIR="/tmp/openclaw"

# ---------------------------------------------------------------------------
# Security: PATH lockdown
# ---------------------------------------------------------------------------
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# ---------------------------------------------------------------------------
# Security: Process limits (prevent fork bombs)
# ---------------------------------------------------------------------------
ulimit -Su 512 2>/dev/null || true
ulimit -Hu 512 2>/dev/null || true

# ---------------------------------------------------------------------------
# Config integrity verification
# ---------------------------------------------------------------------------
verify_config_integrity() {
    if [ ! -f "$HASH_FILE" ]; then
        echo "[shoreguard-start] WARNING: No config hash found, skipping integrity check"
        return 0
    fi
    if ! (cd "$OPENCLAW_HOME" && sha256sum -c "$HASH_FILE" --status 2>/dev/null); then
        echo "[shoreguard-start] FATAL: Config integrity check failed — openclaw.json has been tampered with" >&2
        exit 1
    fi
    echo "[shoreguard-start] Config integrity verified"
}

# ---------------------------------------------------------------------------
# Symlink validation — ensure all symlinks point to .openclaw-data
# ---------------------------------------------------------------------------
validate_symlinks() {
    local ok=true
    for entry in "${OPENCLAW_HOME}"/*; do
        [ -L "$entry" ] || continue
        local target
        target="$(readlink -f "$entry" 2>/dev/null || true)"
        local basename
        basename="$(basename "$entry")"
        local expected="${OPENCLAW_DATA}/${basename}"
        if [ "$target" != "$expected" ]; then
            echo "[shoreguard-start] FATAL: Symlink $entry points to $target, expected $expected" >&2
            ok=false
        fi
    done
    if [ "$ok" = false ]; then
        exit 1
    fi
    echo "[shoreguard-start] Symlinks validated"
}

# ---------------------------------------------------------------------------
# Symlink hardening — make .openclaw directory and symlinks immutable
# ---------------------------------------------------------------------------
harden_symlinks() {
    if ! command -v chattr >/dev/null 2>&1; then
        echo "[shoreguard-start] chattr not available, skipping symlink hardening"
        return 0
    fi
    # Set immutable flag on the directory and all symlinks
    chattr +i "$OPENCLAW_HOME" 2>/dev/null || true
    for entry in "${OPENCLAW_HOME}"/*; do
        [ -L "$entry" ] && chattr +i "$entry" 2>/dev/null || true
    done
    echo "[shoreguard-start] Symlinks hardened with immutable flag"
}

# ---------------------------------------------------------------------------
# Capability dropping — remove unnecessary Linux capabilities
# ---------------------------------------------------------------------------
drop_capabilities() {
    if [ "${SHOREGUARD_CAPS_DROPPED:-}" = "1" ]; then
        return 0
    fi
    if ! command -v capsh >/dev/null 2>&1; then
        echo "[shoreguard-start] capsh not available, skipping capability drop"
        return 0
    fi
    if ! capsh --has-p=cap_setpcap 2>/dev/null; then
        echo "[shoreguard-start] cap_setpcap not available, skipping capability drop"
        return 0
    fi

    export SHOREGUARD_CAPS_DROPPED=1
    exec capsh \
        --drop=cap_net_raw \
        --drop=cap_dac_override \
        --drop=cap_sys_chroot \
        --drop=cap_fsetid \
        --drop=cap_setfcap \
        --drop=cap_mknod \
        --drop=cap_audit_write \
        --drop=cap_net_bind_service \
        -- -c "exec \"$0\" $(printf '%q ' "$@")"
}

# ---------------------------------------------------------------------------
# Setup log directory
# ---------------------------------------------------------------------------
setup_logs() {
    mkdir -p "$LOG_DIR"
    if [ "$(id -u)" = "0" ]; then
        chown gateway:gateway "$LOG_DIR" 2>/dev/null || true
    fi
}

# ---------------------------------------------------------------------------
# Wait for config — ShoreGuard injects openclaw.json after sandbox creation
# ---------------------------------------------------------------------------
wait_for_config() {
    local timeout=120
    local elapsed=0
    while [ ! -f "$CONFIG_FILE" ]; do
        if [ "$elapsed" -ge "$timeout" ]; then
            echo "[shoreguard-start] FATAL: openclaw.json not found after ${timeout}s — did ShoreGuard inject config?" >&2
            exit 1
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done
    echo "[shoreguard-start] Config found: $CONFIG_FILE"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Drop capabilities first (re-execs the script with reduced caps)
drop_capabilities "$@"

echo "[shoreguard-start] Starting ShoreGuard OpenClaw sandbox..."

setup_logs

# Wait for ShoreGuard to inject config
wait_for_config

# Verify config integrity if hash exists
verify_config_integrity

# Validate symlinks point to the right places
validate_symlinks

# Determine if we can use privilege separation
if [ "$(id -u)" = "0" ]; then
    echo "[shoreguard-start] Running with root — enabling privilege separation"

    # Harden symlinks (requires root)
    harden_symlinks

    # Start gateway as gateway user
    echo "[shoreguard-start] Starting gateway as 'gateway' user..."
    exec gosu gateway "$@"
else
    echo "[shoreguard-start] Running as non-root (uid=$(id -u)) — no privilege separation"

    # Best-effort symlink hardening
    harden_symlinks

    # Run directly as current user
    exec "$@"
fi
