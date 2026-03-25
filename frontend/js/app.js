/**
 * Shoreguard — Frontend Application
 * Bootstrap 5 + Vanilla JS control plane for NVIDIA OpenShell
 *
 * Global utilities: health check, apiFetch, confirm/toast, sidebar sandbox list.
 * Page-specific init is handled by each template's DOMContentLoaded handler.
 */

const API = GW ? `/api/gateways/${GW}` : '/api';
const API_GLOBAL = '/api';
function gwUrl(path) { return GW ? `/gateways/${GW}${path}` : path; }
let activeWebSockets = {};
let _gatewayConnected = false;
let _healthInterval = null;
let _initialHealthCheck = true;
// ─── Initialization ──────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    checkGatewayHealth();
    _healthInterval = setInterval(checkGatewayHealth, SG.config.healthCheckInterval);

    // Add body class if subnav is present
    if (document.querySelector('.subnav')) {
        document.body.classList.add('has-subnav');
    }
});

// ─── Navigation ──────────────────────────────────────────────────────────────

function navigateTo(path) {
    window.location.href = path;
}


// ─── Dashboard ───────────────────────────────────────────────────────────────

async function loadDashboard() {
    const container = document.getElementById('dashboard-content');
    if (!container) return;
    container.innerHTML = renderSpinner();

    let providers = [];
    let sandboxes = [];
    let gatewayInfo = null;
    try { providers = await apiFetch(`${API}/providers`); } catch {}
    try { sandboxes = await apiFetch(`${API}/sandboxes`); } catch {}
    try { gatewayInfo = await apiFetch(`${API_GLOBAL}/gateway/info`); } catch {}

    if (sandboxes.length === 0) {
        container.innerHTML = renderGettingStarted(providers.length);
    } else {
        container.innerHTML = renderDashboard(sandboxes, providers, gatewayInfo);
    }
}

function renderDashboard(sandboxes, providers, gatewayInfo) {
    const phases = {};
    sandboxes.forEach(sb => {
        phases[sb.phase] = (phases[sb.phase] || 0) + 1;
    });

    const phaseCards = Object.entries(phases).map(([phase, count]) => {
        const badge = SG.badges.phase[phase] || 'text-bg-secondary';
        return `<span class="badge ${badge} me-2">${count} ${escapeHtml(phase)}</span>`;
    }).join('');

    const gwName = gatewayInfo?.name || 'unknown';
    const gwVersion = gatewayInfo?.version || '';
    const gwStatus = _gatewayConnected
        ? `<span class="badge bg-success"><i class="bi bi-circle-fill me-1"></i>Connected</span>`
        : `<span class="badge bg-danger"><i class="bi bi-circle-fill me-1"></i>Disconnected</span>`;

    return `
        <div class="row g-3 mb-4">
            <div class="col-md-4">
                <div class="card h-100" class="sg-card-themed">
                    <div class="card-body">
                        <h6 class="text-muted mb-2"><i class="bi bi-box me-1"></i>Sandboxes</h6>
                        <div class="fs-3 fw-bold mb-2">${sandboxes.length}</div>
                        <div>${phaseCards}</div>
                    </div>
                    <div class="card-footer border-0 pt-0" style="background:transparent">
                        <a href="${gwUrl('/sandboxes')}" class="text-decoration-none small">View all <i class="bi bi-arrow-right"></i></a>
                    </div>
                </div>
            </div>
            <div class="col-md-4">
                <div class="card h-100" class="sg-card-themed">
                    <div class="card-body">
                        <h6 class="text-muted mb-2"><i class="bi bi-hdd-network me-1"></i>Gateway</h6>
                        <div class="fs-5 fw-bold mb-2">${escapeHtml(gwName)}</div>
                        <div>${gwStatus} ${gwVersion ? `<span class="text-muted small ms-1">${escapeHtml(gwVersion)}</span>` : ''}</div>
                    </div>
                    <div class="card-footer border-0 pt-0" style="background:transparent">
                        <a href="/gateways" class="text-decoration-none small">Manage <i class="bi bi-arrow-right"></i></a>
                    </div>
                </div>
            </div>
            <div class="col-md-4">
                <div class="card h-100" class="sg-card-themed">
                    <div class="card-body">
                        <h6 class="text-muted mb-2"><i class="bi bi-key me-1"></i>Providers</h6>
                        <div class="fs-3 fw-bold mb-2">${providers.length}</div>
                        <div class="text-muted small">${providers.length === 0 ? 'No providers configured' : providers.map(p => escapeHtml(p.type || p.name)).join(', ')}</div>
                    </div>
                    <div class="card-footer border-0 pt-0" style="background:transparent">
                        <a href="/gateways" class="text-decoration-none small">Manage <i class="bi bi-arrow-right"></i></a>
                    </div>
                </div>
            </div>
        </div>

    `;
}

function renderGettingStarted(providerCount) {
    const gwDone = _gatewayConnected;
    const provDone = providerCount > 0;

    return `
        <div class="row justify-content-center">
            <div class="col-lg-8 col-xl-6">
                <div class="card" class="sg-card-themed">
                    <div class="card-body p-4">
                        <h4 class="mb-1">Getting Started</h4>
                        <p class="text-muted small mb-4">Set up your OpenShell environment in 3 steps.</p>

                        <div class="getting-started-step">
                            <div class="getting-started-num ${gwDone ? 'done' : 'pending'}">${gwDone ? '<i class="bi bi-check"></i>' : '1'}</div>
                            <div class="flex-grow-1">
                                <strong>Set up a Gateway</strong>
                                <p class="text-muted small mb-2">The control plane that manages your sandboxes.</p>
                                <a class="btn ${gwDone ? 'btn-outline-success' : 'btn-success'} btn-sm" href="/gateways">
                                    ${gwDone ? '<i class="bi bi-check me-1"></i>Connected' : '<i class="bi bi-arrow-right me-1"></i>Configure'}
                                </a>
                            </div>
                        </div>

                        <div class="getting-started-step">
                            <div class="getting-started-num ${provDone ? 'done' : 'pending'}">${provDone ? '<i class="bi bi-check"></i>' : '2'}</div>
                            <div class="flex-grow-1">
                                <strong>Add a Provider</strong>
                                <p class="text-muted small mb-2">API keys for inference and tools (Claude, OpenAI, NVIDIA, etc.).</p>
                                <a class="btn ${provDone ? 'btn-outline-success' : 'btn-outline-primary'} btn-sm" href="/gateways">
                                    ${provDone ? `<i class="bi bi-check me-1"></i>${providerCount} configured` : '<i class="bi bi-arrow-right me-1"></i>Add Provider'}
                                </a>
                                <span class="text-muted small ms-2">Optional -- sandboxes auto-create on demand</span>
                            </div>
                        </div>

                        <div class="getting-started-step">
                            <div class="getting-started-num pending">3</div>
                            <div class="flex-grow-1">
                                <strong>Create a Sandbox</strong>
                                <p class="text-muted small mb-2">An isolated environment for your AI agent.</p>
                                <a class="btn btn-outline-primary btn-sm" href="/gateways" ${!gwDone ? 'class="disabled" aria-disabled="true"' : ''}>
                                    <i class="bi bi-arrow-right me-1"></i>Create Sandbox
                                </a>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;
}

// ─── Gateway Health ──────────────────────────────────────────────────────────

async function checkGatewayHealth() {
    const badge = document.getElementById('gateway-status');
    const mobileBadge = document.getElementById('gateway-status-mobile');
    const banner = document.getElementById('gateway-error-banner');

    // No health check on global pages (no gateway context)
    if (!GW) return;
    if (!badge) return;

    function setBadge(cls, html, mobileHtml) {
        badge.className = `sidebar-status badge ${cls}`;
        badge.innerHTML = html;
        if (mobileBadge) {
            mobileBadge.className = `badge ${cls}`;
            mobileBadge.innerHTML = mobileHtml || html;
        }
    }

    try {
        const resp = await fetch(`${API}/health`);
        if (resp.ok) {
            const data = await resp.json();
            try {
                const info = await fetch(`${API_GLOBAL}/gateway/info`).then(r => r.json());
                const gwName = info.name || '';
                setBadge('bg-success',
                    `<i class="bi bi-circle-fill me-1"></i>${gwName ? escapeHtml(gwName) : ''} ${data.version || 'Connected'}`,
                    '<i class="bi bi-circle-fill"></i>');
            } catch {
                setBadge('bg-success',
                    `<i class="bi bi-circle-fill me-1"></i>${data.version || 'Connected'}`,
                    '<i class="bi bi-circle-fill"></i>');
            }

            if (!_gatewayConnected) {
                _gatewayConnected = true;
                if (banner) {
                    banner.style.display = 'none';
                    banner.style.setProperty('display', 'none', 'important');
                }
                clearInterval(_healthInterval);
                _healthInterval = setInterval(checkGatewayHealth, SG.config.healthCheckInterval);
                if (!_initialHealthCheck) showToast('Gateway connected.', 'success');
                _initialHealthCheck = false;
            }
        } else {
            throw new Error('Degraded');
        }
    } catch (e) {
        const wasDegraded = e.message === 'Degraded';
        setBadge(wasDegraded ? 'bg-warning' : 'bg-danger',
            `<i class="bi bi-circle-fill me-1"></i>${wasDegraded ? 'Degraded' : 'Disconnected'}`,
            '<i class="bi bi-circle-fill"></i>');

        if (banner) {
            banner.style.display = '';
            banner.style.removeProperty('display');
        }
        const detail = document.getElementById('gateway-error-detail');
        if (detail) detail.textContent = wasDegraded ? 'Gateway is degraded.' : 'Attempting to reconnect...';

        if (_gatewayConnected) {
            _gatewayConnected = false;
            clearInterval(_healthInterval);
            _healthInterval = setInterval(checkGatewayHealth, SG.config.healthCheckFallback);
        }
    }
}

// ─── API Fetch Wrapper ───────────────────────────────────────────────────────

async function apiFetch(url, options = {}) {
    try {
        const resp = await fetch(url, options);
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
        if (e.message && !e.message.startsWith('HTTP')) {
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

// ─── Toast Notifications ─────────────────────────────────────────────────────

function showToast(message, type = 'info') {
    const container = document.getElementById('approval-toasts');
    const toastId = `toast-${Date.now()}`;
    const iconMap = {
        success: 'check-circle-fill text-success',
        danger: 'x-circle-fill text-danger',
        warning: 'exclamation-triangle-fill text-warning',
        info: 'info-circle-fill text-info',
    };
    const icon = iconMap[type] || iconMap.info;

    container.insertAdjacentHTML('beforeend', `
        <div id="${toastId}" class="toast" role="alert">
            <div class="toast-body d-flex align-items-center gap-2">
                <i class="bi bi-${icon}"></i>
                <span>${escapeHtml(message)}</span>
            </div>
        </div>`);

    const toastEl = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastEl, { autohide: true, delay: SG.config.toastDelay });
    toast.show();
    toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
}

// ─── Utilities ───────────────────────────────────────────────────────────────

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML;
}

function formatTimestamp(ms) {
    if (!ms) return '';
    return new Date(ms).toLocaleString();
}
