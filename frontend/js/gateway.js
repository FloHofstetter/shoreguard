/**
 * Shoreguard v0.3 — Multi-Gateway Management
 * Register/unregister remote gateways, test connections, inference config.
 */

// Gateway type icons and inference providers loaded from constants/API
let _knownProviders = [];

// ─── Gateway List Page ──────────────────────────────────────────────────────

async function loadGatewayPage() {
    const container = document.getElementById('gateway-page-content');
    container.innerHTML = renderSpinner('Loading gateways...');

    // Load inference providers from API if not cached (only when a gateway is selected)
    if (_knownProviders.length === 0 && GW) {
        try { _knownProviders = await apiFetch(`${API}/providers/inference-providers`); } catch {}
    }

    try {
        const gateways = await apiFetch(`${API_GLOBAL}/gateway/list`);

        if (gateways.length === 0) {
            container.innerHTML = `
                <div class="text-center text-muted py-5">
                    <i class="bi bi-hdd-network fs-1 d-block mb-3"></i>
                    <p>No gateways registered.</p>
                    ${_sgHasRole('admin') ? `<button class="btn btn-success btn-sm" data-bs-toggle="modal" data-bs-target="#registerGatewayModal">
                        <i class="bi bi-plus me-1"></i>Register Gateway
                    </button>` : ''}
                </div>
            `;
            return;
        }

        container.innerHTML = `
            <div class="table-responsive mb-4">
                <table class="table table-dark table-striped table-hover table-sm align-middle table-clickable">
                    <thead>
                        <tr>
                            <th>Name</th>
                            <th>Endpoint</th>
                            <th>Auth</th>
                            <th>Status</th>
                            <th class="d-none d-md-table-cell">Last Seen</th>
                            <th class="text-end" style="width:60px"></th>
                        </tr>
                    </thead>
                    <tbody>
                        ${gateways.map(gw => `
                            <tr onclick="navigateTo('/gateways/${escapeHtml(gw.name)}')" style="cursor:pointer"
                                class="${gw.active ? 'table-active' : ''}">
                                <td>
                                    <strong>${escapeHtml(gw.name)}</strong>
                                </td>
                                <td class="font-monospace small">${escapeHtml(gw.endpoint || '—')}</td>
                                <td class="small">${escapeHtml(gw.auth_mode || '—')}</td>
                                <td>${renderGatewayStatusBadge(gw)}</td>
                                <td class="d-none d-md-table-cell small text-muted">${gw.last_seen ? formatTimeAgo(gw.last_seen) : '—'}</td>
                                <td class="text-end" onclick="event.stopPropagation()">
                                    ${_sgHasRole('admin') ? `<button class="btn btn-sm text-muted delete-btn" onclick="unregisterGateway('${escapeHtml(gw.name)}')" title="Unregister">
                                        <i class="bi bi-trash3"></i>
                                    </button>` : ''}
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        `;
    } catch (e) {
        container.innerHTML = `<div class="alert alert-danger">${escapeHtml(e.message)}</div>`;
    }
}

// ─── Gateway Detail Page ────────────────────────────────────────────────────

async function loadGatewayDetail(name) {
    const container = document.getElementById('gateway-detail-content');
    container.innerHTML = renderSpinner();

    try {
        const gateways = await apiFetch(`${API_GLOBAL}/gateway/list`);
        const gw = gateways.find(g => g.name === name);
        if (!gw) {
            container.innerHTML = `<div class="alert alert-warning">Gateway "${escapeHtml(name)}" not found.</div>`;
            return;
        }

        container.innerHTML = `
            <!-- Status -->
            <div class="d-flex align-items-center justify-content-between mb-4 pb-3 border-bottom" style="border-color:var(--sg-border)!important">
                <div class="d-flex align-items-center gap-3">
                    ${renderGatewayStatusBadge(gw)}
                    ${gw.version ? `<span class="text-muted">v${escapeHtml(gw.version)}</span>` : ''}
                </div>
                <div class="d-flex gap-2">
                    <button class="btn btn-outline-primary btn-sm" onclick="testConnection('${escapeHtml(name)}')">
                        <i class="bi bi-plug me-1"></i>Test Connection
                    </button>
                    <button class="btn btn-outline-secondary btn-sm" onclick="loadGatewayDetail('${escapeHtml(name)}')" title="Refresh">
                        <i class="bi bi-arrow-clockwise"></i>
                    </button>
                </div>
            </div>
            <div id="gw-action-output" class="mb-3"></div>

            <!-- Quick links -->
            <div class="row g-3 mb-4">
                <div class="col">
                    <a href="${gwUrl('/sandboxes')}" class="btn btn-outline-secondary w-100 py-3">
                        <i class="bi bi-grid me-2"></i>Sandboxes
                    </a>
                </div>
                <div class="col">
                    <a href="${gwUrl('/providers')}" class="btn btn-outline-secondary w-100 py-3">
                        <i class="bi bi-key me-2"></i>Providers
                    </a>
                </div>
                <div class="col">
                    <a href="${gwUrl('/wizard')}" class="btn btn-outline-success w-100 py-3">
                        <i class="bi bi-plus-circle me-2"></i>New Sandbox
                    </a>
                </div>
            </div>

            <!-- Details -->
            <h6 class="text-muted mb-3">Details</h6>
            <dl class="row mb-4">
                <dt class="col-sm-3 text-muted fw-normal">Endpoint</dt>
                <dd class="col-sm-9 font-monospace small">${escapeHtml(gw.endpoint || '—')}</dd>
                <dt class="col-sm-3 text-muted fw-normal">Scheme</dt>
                <dd class="col-sm-9">${escapeHtml(gw.scheme || '—')}</dd>
                ${gw.auth_mode ? `
                <dt class="col-sm-3 text-muted fw-normal">Auth</dt>
                <dd class="col-sm-9">${escapeHtml(gw.auth_mode)}</dd>` : ''}
                ${gw.registered_at ? `
                <dt class="col-sm-3 text-muted fw-normal">Registered</dt>
                <dd class="col-sm-9 small text-muted">${escapeHtml(gw.registered_at)}</dd>` : ''}
                ${gw.last_seen ? `
                <dt class="col-sm-3 text-muted fw-normal">Last Seen</dt>
                <dd class="col-sm-9 small text-muted">${formatTimeAgo(gw.last_seen)}</dd>` : ''}
            </dl>

            <div class="border-top pt-3" style="border-color:var(--sg-border)!important">
                <button class="btn btn-outline-danger btn-sm" onclick="unregisterGateway('${escapeHtml(name)}')">
                    <i class="bi bi-trash me-1"></i>Unregister Gateway
                </button>
            </div>

            <!-- Inference Provider (only for active gateway) -->
            ${gw.active ? `
            <div class="card mb-4 sg-card-themed">
                <div class="card-body">
                    <h6 class="text-muted mb-3"><i class="bi bi-cpu me-2"></i>Inference Provider</h6>
                    <div id="inference-config">
                        ${gw.connected
                            ? '<div class="text-muted small"><div class="spinner-border spinner-border-sm me-2"></div>Loading provider config...</div>'
                            : '<div class="text-muted small">Connect to gateway to configure inference provider.</div>'}
                    </div>
                </div>
            </div>` : ''}
        `;

        // Load inference config if this is the active connected gateway
        if (gw.active && gw.connected) loadInferenceConfig();

    } catch (e) {
        container.innerHTML = `<div class="alert alert-danger">${escapeHtml(e.message)}</div>`;
    }
}

// ─── Gateway Actions ────────────────────────────────────────────────────────

async function testConnection(name) {
    const output = document.getElementById('gw-action-output');
    output.innerHTML = `
        <div class="log-output small">
            <div class="log-line"><div class="spinner-border spinner-border-sm me-2"></div>Testing connection...</div>
        </div>`;

    try {
        const result = await apiFetch(`${API_GLOBAL}/gateway/${name}/test-connection`, { method: 'POST' });

        if (result.success) {
            output.innerHTML = `
                <div class="log-output small">
                    <div class="log-line log-info">Connected! ${result.version ? `v${escapeHtml(result.version)}` : ''} (${escapeHtml(result.health_status || 'ok')})</div>
                </div>`;
            showToast('Connection successful.', 'success');
            setTimeout(() => loadGatewayDetail(name), SG.config.actionRefreshDelay);
        } else {
            output.innerHTML = `
                <div class="log-output small">
                    <div class="log-line log-error">Connection failed: ${escapeHtml(result.error || 'Unknown error')}</div>
                </div>`;
            showToast('Connection failed.', 'danger');
        }
    } catch (e) {
        output.innerHTML = `
            <div class="log-output small">
                <div class="log-line log-error">Error: ${escapeHtml(e.message)}</div>
            </div>`;
    }
}

async function unregisterGateway(name) {
    const confirmed = await showConfirm(
        `Unregister gateway "${name}"? This removes it from Shoreguard but does not affect the running gateway.`,
        { icon: 'trash', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Unregister' }
    );
    if (!confirmed) return;
    try {
        const result = await apiFetch(`${API_GLOBAL}/gateway/${name}`, { method: 'DELETE' });
        if (result.success) {
            showToast(`Gateway "${name}" unregistered.`, 'success');
            checkGatewayHealth();
            navigateTo('/gateways');
        } else {
            showToast(`Failed: ${result.error}`, 'danger');
        }
    } catch (e) {
        showToast(`Error: ${e.message}`, 'danger');
    }
}

async function registerGateway(e) {
    if (e) e.preventDefault();

    const name = document.getElementById('reg-gw-name').value.trim();
    const endpoint = document.getElementById('reg-gw-endpoint').value.trim();
    const scheme = document.getElementById('reg-gw-scheme').value;
    const authMode = document.getElementById('reg-gw-auth-mode').value;
    const gpu = document.getElementById('reg-gw-gpu').checked;
    const output = document.getElementById('register-gw-output');
    const btn = document.getElementById('register-gw-btn');

    if (!name) {
        output.innerHTML = '<div class="text-danger small">Name is required.</div>';
        return;
    }
    if (!endpoint) {
        output.innerHTML = '<div class="text-danger small">Endpoint is required.</div>';
        return;
    }

    btn.disabled = true;
    output.innerHTML = '<div class="text-muted small"><div class="spinner-border spinner-border-sm me-2"></div>Registering gateway...</div>';

    try {
        const body = { name, endpoint, scheme, auth_mode: authMode, metadata: { gpu } };

        // Read certificates if provided
        const caFile = document.getElementById('reg-gw-ca').files[0];
        const certFile = document.getElementById('reg-gw-cert').files[0];
        const keyFile = document.getElementById('reg-gw-key').files[0];

        if (caFile) body.ca_cert = await readFileAsBase64(caFile);
        if (certFile) body.client_cert = await readFileAsBase64(certFile);
        if (keyFile) body.client_key = await readFileAsBase64(keyFile);

        const result = await apiFetch(`${API_GLOBAL}/gateway/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });

        output.innerHTML = '<div class="text-success small"><i class="bi bi-check-circle me-1"></i>Gateway registered!</div>';
        showToast(`Gateway "${name}" registered.`, 'success');
        bootstrap.Modal.getInstance(document.getElementById('registerGatewayModal'))?.hide();
        checkGatewayHealth();
        loadGatewayPage();
    } catch (e) {
        output.innerHTML = `<div class="text-danger small">${escapeHtml(e.message)}</div>`;
    } finally {
        btn.disabled = false;
    }
}

function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            // Strip data URL prefix to get raw base64
            const base64 = reader.result.split(',')[1];
            resolve(base64);
        };
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });
}

// ─── Inference Provider Config ──────────────────────────────────────────────

async function loadInferenceConfig() {
    const container = document.getElementById('inference-config');
    if (!container) return;

    try {
        const config = await apiFetch(`${API}/inference`);
        renderInferenceForm(container, config);
    } catch (e) {
        renderInferenceForm(container, { provider_name: '', model_id: '' });
    }
}

function renderInferenceForm(container, config) {
    const currentProvider = _knownProviders.find(p => p.name === config.provider_name);
    const placeholder = currentProvider?.placeholder || 'model-id';

    container.innerHTML = `
        <form onsubmit="saveInferenceConfig(event)" class="row g-3 align-items-end">
            <div class="col-md-4">
                <label class="form-label small text-muted">Provider</label>
                <select class="form-select form-select-sm" id="inf-provider" onchange="onProviderChange()">
                    <option value="" ${!config.provider_name ? 'selected' : ''}>— Select —</option>
                    ${_knownProviders.map(p => `
                        <option value="${p.name}" ${config.provider_name === p.name ? 'selected' : ''}>
                            ${p.label}
                        </option>
                    `).join('')}
                </select>
            </div>
            <div class="col-md-5">
                <label class="form-label small text-muted">Model ID</label>
                <input type="text" class="form-control form-control-sm" id="inf-model"
                       value="${escapeHtml(config.model_id || '')}"
                       placeholder="${placeholder}">
            </div>
            <div class="col-md-3 d-flex gap-2">
                <button type="submit" class="btn btn-primary btn-sm" id="inf-save-btn">
                    <i class="bi bi-check-lg me-1"></i>Save
                </button>
            </div>
        </form>
        ${config.provider_name ? `
        <div class="mt-2 small text-muted">
            <i class="bi bi-info-circle me-1"></i>
            API key must be set as environment variable
            ${currentProvider ? `<code>${currentProvider.env_var}</code>` : ''}
            on the gateway host.
        </div>` : `
        <div class="mt-2 small text-warning">
            <i class="bi bi-exclamation-triangle me-1"></i>
            No inference provider configured. Sandboxes need a provider to run agents.
        </div>`}
        <div id="inf-save-output" class="mt-2"></div>
    `;
}

function onProviderChange() {
    const select = document.getElementById('inf-provider');
    const modelInput = document.getElementById('inf-model');
    const provider = _knownProviders.find(p => p.name === select.value);
    if (provider) {
        modelInput.placeholder = provider.placeholder;
        if (!modelInput.value) modelInput.value = provider.placeholder;
    }
}

async function saveInferenceConfig(e) {
    e.preventDefault();
    const provider = document.getElementById('inf-provider').value;
    const model = document.getElementById('inf-model').value;
    const output = document.getElementById('inf-save-output');
    const saveBtn = document.getElementById('inf-save-btn');

    if (!provider) {
        output.innerHTML = '<div class="text-danger small">Select a provider.</div>';
        return;
    }
    if (!model) {
        output.innerHTML = '<div class="text-danger small">Enter a model ID.</div>';
        return;
    }

    saveBtn.disabled = true;
    output.innerHTML = '<div class="text-muted small"><div class="spinner-border spinner-border-sm me-2"></div>Saving...</div>';

    try {
        await apiFetch(`${API}/inference`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                provider_name: provider,
                model_id: model,
                verify: false,
            }),
        });
        output.innerHTML = '<div class="text-success small"><i class="bi bi-check-circle me-1"></i>Provider configured.</div>';
        showToast('Inference provider saved.', 'success');
    } catch (e) {
        output.innerHTML = `<div class="text-danger small"><i class="bi bi-x-circle me-1"></i>${escapeHtml(e.message)}</div>`;
        showToast('Failed to save provider.', 'danger');
    } finally {
        saveBtn.disabled = false;
    }
}
