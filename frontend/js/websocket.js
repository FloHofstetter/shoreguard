/**
 * Shoreguard — WebSocket
 * Live event streaming with auto-reconnect for sandbox status,
 * logs, platform events, and draft policy updates.
 */

const _wsReconnectState = {};

function connectWebSocket(sandboxName, sandboxId) {
    if (activeWebSockets[sandboxName]) {
        activeWebSockets[sandboxName].close();
    }
    _wsReconnectState[sandboxName] = { attempts: 0, sandboxId };

    _doConnect(sandboxName, sandboxId);
}

function _doConnect(sandboxName, sandboxId) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/${GW}/${sandboxName}`);

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        // Reset reconnect counter on successful message
        if (_wsReconnectState[sandboxName]) _wsReconnectState[sandboxName].attempts = 0;
        handleWebSocketEvent(sandboxName, data);
    };

    ws.onclose = () => {
        delete activeWebSockets[sandboxName];
        // Reconnect if still on detail page for this sandbox
        if (window.location.pathname.startsWith('/sandboxes/')) {
            const state = _wsReconnectState[sandboxName];
            if (state) {
                state.attempts++;
                const backoff = Math.min(1000 * Math.pow(2, state.attempts - 1), SG.config.wsMaxBackoff);
                setTimeout(() => {
                    if (window.location.pathname.startsWith('/sandboxes/')) {
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
    // Live status updates
    if (event.type === 'status' && event.data) {
        const phaseBadge = document.getElementById('detail-phase-badge');
        if (phaseBadge) {
            const badgeClass = SG.badges.phase[event.data.phase] || 'text-bg-secondary';
            phaseBadge.className = `badge ${badgeClass}`;
            phaseBadge.textContent = event.data.phase;
        }
        const policyVersion = document.getElementById('detail-policy-version');
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
        // Auto-refresh approvals tab if currently visible
        const activeTab = document.querySelector('.detail-tabs .nav-link.active');
        if (activeTab && activeTab.textContent.includes('Approvals')) {
            const content = document.getElementById('detail-tab-content');
            if (content) loadApprovalsTab(sandboxName, content);
        }
    }

    // Live log streaming
    if (event.type === 'log' && window.location.pathname.startsWith('/sandboxes/')) {
        const logContainer = document.getElementById('log-container');
        if (logContainer) {
            const level = event.data.level?.toLowerCase() || 'info';
            logContainer.innerHTML += `<div class="log-line log-${level}"><span class="text-muted">${formatTimestamp(event.data.timestamp_ms)}</span> <span class="badge text-bg-secondary me-1">${event.data.source || 'gateway'}</span> ${escapeHtml(event.data.message)}</div>`;
            logContainer.scrollTop = logContainer.scrollHeight;
        }
    }

    // Platform events
    if (event.type === 'event' && window.location.pathname.startsWith('/sandboxes/')) {
        const logContainer = document.getElementById('log-container');
        if (logContainer) {
            logContainer.innerHTML += `<div class="log-line log-warn"><span class="text-muted">${formatTimestamp(event.data.timestamp_ms)}</span> <span class="badge text-bg-warning me-1">${event.data.type || 'event'}</span> ${escapeHtml(event.data.message)}</div>`;
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
                    <button class="btn btn-warning btn-sm" onclick="navigateTo(gwUrl('/sandboxes/${sandboxName}'))">
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
