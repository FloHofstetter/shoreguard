/**
 * Shoreguard — WebSocket
 * Live event streaming with auto-reconnect for sandbox status,
 * logs, platform events, and draft policy updates.
 */

const activeWebSockets = {};
const _wsReconnectState = {};

function connectWebSocket(sandboxName, sandboxId) {
    if (activeWebSockets[sandboxName]) {
        activeWebSockets[sandboxName].close();
    }
    _wsReconnectState[sandboxName] = { attempts: 0, sandboxId };

    _doConnect(sandboxName, sandboxId);
}

function _dispatchWsState(sandboxName, state) {
    document.dispatchEvent(new CustomEvent('sg:ws-state', {
        detail: { sandboxName, state },
    }));
}

function _doConnect(sandboxName, sandboxId) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/${GW}/${sandboxName}`);
    let heartbeatTimer = null;

    function resetHeartbeatTimer() {
        clearTimeout(heartbeatTimer);
        heartbeatTimer = setTimeout(() => {
            console.warn(`WebSocket: heartbeat timeout for ${sandboxName}`);
            ws.close();
        }, SG.config.wsHeartbeatTimeout);
    }

    ws.onopen = () => {
        resetHeartbeatTimer();
        _dispatchWsState(sandboxName, 'connected');
    };

    ws.onmessage = (event) => {
        let data;
        try {
            data = JSON.parse(event.data);
        } catch {
            console.warn('WebSocket: failed to parse message', event.data);
            return;
        }
        resetHeartbeatTimer();
        // Reset reconnect counter on successful message
        if (_wsReconnectState[sandboxName]) _wsReconnectState[sandboxName].attempts = 0;

        // Heartbeats are keepalives only — don't pass to event handler
        if (data.type === 'heartbeat') return;

        handleWebSocketEvent(sandboxName, data);
    };

    ws.onclose = () => {
        clearTimeout(heartbeatTimer);
        delete activeWebSockets[sandboxName];
        _dispatchWsState(sandboxName, 'disconnected');

        // Reconnect if still on detail page for this sandbox
        if (window.location.pathname.includes('/sandboxes/')) {
            const state = _wsReconnectState[sandboxName];
            if (state) {
                state.attempts++;
                if (state.attempts > SG.config.wsMaxRetries) {
                    console.error(`WebSocket: max retries reached for ${sandboxName}`);
                    _dispatchWsState(sandboxName, 'failed');
                    showToast(`Live updates unavailable for ${sandboxName}. Refresh to retry.`, 'warning');
                    return;
                }
                _dispatchWsState(sandboxName, 'reconnecting');
                const backoff = Math.min(1000 * Math.pow(2, state.attempts - 1), SG.config.wsMaxBackoff);
                setTimeout(() => {
                    if (window.location.pathname.includes('/sandboxes/')) {
                        _doConnect(sandboxName, state.sandboxId);
                    }
                }, backoff);
            }
        }
    };

    ws.onerror = () => {
        // Only toast on first error, not every reconnect attempt
        const state = _wsReconnectState[sandboxName];
        if (state && state.attempts === 0) {
            showToast(`WebSocket error for ${sandboxName}`, 'warning');
        }
    };

    activeWebSockets[sandboxName] = ws;
}

function handleWebSocketEvent(sandboxName, event) {
    // Live status updates — dispatch Alpine event for reactive components
    if (event.type === 'status' && event.data) {
        document.dispatchEvent(new CustomEvent('sg:sandbox-status', {
            detail: { sandboxName, ...event.data },
        }));

        // Legacy DOM update for subnav phase badge
        const phaseBadge = document.getElementById('sandbox-phase-badge');
        if (phaseBadge) {
            const badgeClass = SG.badges.phase[event.data.phase] || 'text-bg-secondary';
            phaseBadge.className = `badge ${badgeClass}`;
            phaseBadge.textContent = event.data.phase;
        }
        const policyVersion = document.getElementById('sandbox-policy-version');
        if (policyVersion && event.data.current_policy_version !== undefined) {
            policyVersion.textContent = `v${event.data.current_policy_version}`;
        }
        if (event.data.phase === 'error') {
            showToast(`Sandbox ${sandboxName} entered error state.`, 'danger');
        }
    }

    // Draft policy updates
    if (event.type === 'draft_policy_update' && event.data.total_pending > 0) {
        showApprovalToast(sandboxName, event.data);
        // Dispatch Alpine event for approvals page to auto-refresh
        document.dispatchEvent(new CustomEvent('sg:approvals-update', {
            detail: { sandboxName, ...event.data },
        }));
    }

    // Live log streaming
    if (event.type === 'log' && window.location.pathname.includes('/sandboxes/')) {
        const logContainer = document.getElementById('log-container');
        if (logContainer) {
            const rawLevel = event.data.level?.toLowerCase() || 'info';
            const level = ['debug', 'info', 'warn', 'warning', 'error', 'critical'].includes(rawLevel) ? rawLevel : 'info';
            logContainer.insertAdjacentHTML('beforeend', `<div class="log-line log-${level}"><span class="text-muted">${formatTimestamp(event.data.timestamp_ms)}</span> <span class="badge text-bg-secondary me-1">${escapeHtml(event.data.source || 'gateway')}</span> ${escapeHtml(event.data.message)}</div>`);
            logContainer.scrollTop = logContainer.scrollHeight;
        }
    }

    // Platform events
    if (event.type === 'event' && window.location.pathname.includes('/sandboxes/')) {
        const logContainer = document.getElementById('log-container');
        if (logContainer) {
            logContainer.insertAdjacentHTML('beforeend', `<div class="log-line log-warn"><span class="text-muted">${formatTimestamp(event.data.timestamp_ms)}</span> <span class="badge text-bg-warning me-1">${escapeHtml(event.data.type || 'event')}</span> ${escapeHtml(event.data.message)}</div>`);
            logContainer.scrollTop = logContainer.scrollHeight;
        }
    }

    // Warnings
    if (event.type === 'warning') {
        showToast(event.data.message || 'Gateway warning', 'warning');
    }
}

function showApprovalToast(sandboxName, data) {
    const container = document.getElementById('approval-toasts');
    const toastId = `toast-${Date.now()}`;
    container.insertAdjacentHTML('beforeend', `
        <div id="${toastId}" class="toast" role="alert">
            <div class="toast-header">
                <i class="bi bi-shield-exclamation text-warning me-2"></i>
                <strong class="me-auto">${escapeHtml(sandboxName)}</strong>
                <small>just now</small>
                <button type="button" class="btn-close" data-bs-dismiss="toast"></button>
            </div>
            <div class="toast-body">
                ${data.total_pending} pending approval(s).
                ${data.summary ? `<br><small class="text-muted">${escapeHtml(data.summary)}</small>` : ''}
                <div class="mt-2">
                    <button class="btn btn-warning btn-sm" onclick="navigateTo(gwUrl('/sandboxes/${escapeHtml(sandboxName)}'))">
                        Review
                    </button>
                </div>
            </div>
        </div>`);
    const toastEl = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastEl, { autohide: true, delay: SG.config.approvalToastDelay });
    toast.show();
    toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
}
