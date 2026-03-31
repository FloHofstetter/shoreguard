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
