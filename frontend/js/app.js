/**
 * Shoreguard — Frontend Application
 * Bootstrap 5 + Alpine.js control plane for NVIDIA OpenShell
 *
 * Global utilities: apiFetch, confirm modal, escapeHtml.
 * Health check, auth, and toasts are managed by Alpine stores (alpine-init.js).
 * Page-specific logic is handled by Alpine components in each template.
 */

const API = GW ? `/api/gateways/${GW}` : '/api';
const API_GLOBAL = '/api';
function gwUrl(path) { return GW ? `/gateways/${GW}${path}` : path; }

// ─── Navigation ──────────────────────────────────────────────────────────

function navigateTo(path) {
    window.location.href = path;
}

// ─── API Fetch Wrapper ───────────────────────────────────────────────────────

async function apiFetch(url, options = {}) {
    try {
        const resp = await fetch(url, options);
        if (resp.status === 401) {
            window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
            throw new Error('Authentication required');
        }
        if (!resp.ok) {
            let detail = '';
            try {
                const body = await resp.json();
                detail = body.detail || JSON.stringify(body);
            } catch {
                detail = await resp.text();
            }
            throw new Error(detail || `HTTP ${resp.status}`);
        }
        const text = await resp.text();
        return text ? JSON.parse(text) : null;
    } catch (e) {
        if (e.message && !e.message.startsWith('HTTP') && e.message !== 'Authentication required') {
            if (e instanceof TypeError && e.message.includes('fetch')) {
                throw new Error('Network error — is the gateway running?');
            }
        }
        throw e;
    }
}

// ─── Operation Polling ──────────────────────────────────────────────────────

const _ACTIVE_STATUSES = new Set(['pending', 'running', 'cancelling']);

/**
 * Wait for a long-running operation to reach a terminal state.
 *
 * Tries Server-Sent Events first for real-time updates.  Falls back to
 * long-poll requests if SSE is unavailable or errors out.
 *
 * @param {string} operationId - The operation ID to poll.
 * @param {Object} [options] - Polling options.
 * @param {number} [options.timeoutMs=300000] - Maximum wait time (5 minutes).
 * @param {Function} [options.onProgress] - Callback for progress updates (pct, message).
 * @returns {Promise<Object>} The completed operation.
 */
async function pollOperation(operationId, { timeoutMs = 300000, onProgress } = {}) {
    if (typeof EventSource !== 'undefined') {
        try {
            return await _pollSSE(operationId, { timeoutMs, onProgress });
        } catch {
            // SSE failed — fall through to long-poll.
        }
    }
    return _pollLongPoll(operationId, { timeoutMs, onProgress });
}

function _pollSSE(operationId, { timeoutMs, onProgress }) {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
            source.close();
            reject(new Error('Operation timed out waiting for completion'));
        }, timeoutMs);
        const source = new EventSource(`/api/operations/${operationId}/stream`);
        source.onmessage = (event) => {
            const op = JSON.parse(event.data);
            if (onProgress && op.progress > 0) onProgress(op.progress, op.progress_message);
            if (!_ACTIVE_STATUSES.has(op.status)) {
                source.close();
                clearTimeout(timer);
                resolve(op);
            }
        };
        source.onerror = () => {
            source.close();
            clearTimeout(timer);
            reject(new Error('SSE connection failed'));
        };
    });
}

async function _pollLongPoll(operationId, { timeoutMs = 300000, onProgress } = {}) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        const remainingSecs = Math.min(30, Math.ceil((deadline - Date.now()) / 1000));
        const op = await apiFetch(`/api/operations/${operationId}?wait=${remainingSecs}`);
        if (onProgress && op.progress > 0) onProgress(op.progress, op.progress_message);
        if (!_ACTIVE_STATUSES.has(op.status)) return op;
    }
    throw new Error('Operation timed out waiting for completion');
}

// ─── Confirm Modal ───────────────────────────────────────────────────────────

function showConfirm(message, { icon = 'exclamation-triangle', iconColor = 'text-warning', btnClass = 'btn-danger', btnLabel = 'Confirm' } = {}) {
    return new Promise((resolve) => {
        document.getElementById('confirm-icon').className = `bi bi-${icon} fs-1 ${iconColor} d-block mb-3`;
        document.getElementById('confirm-message').textContent = message;
        const btn = document.getElementById('confirm-action');
        btn.className = `btn btn-sm ${btnClass}`;
        btn.textContent = btnLabel;

        const modal = new bootstrap.Modal(document.getElementById('confirmModal'));

        function onConfirm() {
            btn.removeEventListener('click', onConfirm);
            modal.hide();
            resolve(true);
        }

        btn.addEventListener('click', onConfirm);
        document.getElementById('confirmModal').addEventListener('hidden.bs.modal', () => {
            btn.removeEventListener('click', onConfirm);
            resolve(false);
        }, { once: true });

        modal.show();
    });
}

// ─── Utilities ───────────────────────────────────────────────────────────────

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML.replace(/'/g, '&#39;');
}

function formatTimestamp(ms) {
    if (!ms) return '';
    return new Date(ms).toLocaleString();
}

// ─── Keyboard Shortcuts ─────────────────────────────────────────────────────

let _pendingKey = null;

document.addEventListener('keydown', (e) => {
    // Don't trigger when typing in inputs
    if (e.target.matches('input, textarea, select, [contenteditable]')) return;

    // "g" prefix for go-to navigation
    if (_pendingKey === 'g') {
        _pendingKey = null;
        const routes = {
            d: '/',
            g: '/gateways',
            a: '/audit',
            u: '/users',
            p: '/policies',
            r: '/groups',
        };
        if (GW) routes.s = gwUrl('/sandboxes');
        if (routes[e.key]) { e.preventDefault(); navigateTo(routes[e.key]); }
        return;
    }

    if (e.key === 'g' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        _pendingKey = 'g';
        setTimeout(() => { _pendingKey = null; }, 1000);
        return;
    }

    if (e.key === '?' && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        const modal = document.getElementById('shortcutsModal');
        if (modal) new bootstrap.Modal(modal).show();
    }
});
