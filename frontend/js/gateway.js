/**
 * Shoreguard — Multi-Gateway Management
 * List, detail, create, start/stop/restart/destroy, inference config.
 */

// Gateway type icons and inference providers loaded from constants/API
let _knownProviders = [];

// ─── Gateway List Page ──────────────────────────────────────────────────────

async function loadGatewayPage() {
    const container = document.getElementById('gateway-page-content');
    container.innerHTML = renderSpinner('Loading gateways...');

    // Load inference providers from API if not cached
    if (_knownProviders.length === 0) {
        try { _knownProviders = await apiFetch(`${API}/providers/inference-providers`); } catch {}
    }

    try {
        const [gateways, diag] = await Promise.all([
            apiFetch(`${API_GLOBAL}/gateway/list`),
            apiFetch(`${API_GLOBAL}/gateway/diagnostics`),
        ]);

        if (gateways.length === 0) {
            container.innerHTML = `
                <div class="text-center text-muted py-5">
                    <i class="bi bi-hdd-network fs-1 d-block mb-3"></i>
                    <p>No gateways configured.</p>
                    <button class="btn btn-success btn-sm" data-bs-toggle="modal" data-bs-target="#createGatewayModal">
                        <i class="bi bi-plus me-1"></i>Create Gateway
                    </button>
                </div>
                ${renderDiagnostics(diag)}
            `;
            return;
        }

        container.innerHTML = `
            <div class="table-responsive mb-4">
                <table class="table table-dark table-striped table-hover table-sm align-middle table-clickable">
                    <thead>
                        <tr>
                            <th>Name</th>
                            <th>Type</th>
                            <th>Endpoint</th>
                            <th class="d-none d-md-table-cell">Port</th>
                            <th>Status</th>
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
                                <td>${renderGatewayTypeIcon(gw.type)}</td>
                                <td class="font-monospace small">${escapeHtml(gw.endpoint || '—')}</td>
                                <td class="d-none d-md-table-cell">${gw.port || '—'}</td>
                                <td>${renderGatewayStatusBadge(gw)}</td>
                                <td class="text-end" onclick="event.stopPropagation()">
                                    <button class="btn btn-sm text-muted delete-btn" onclick="destroyGateway('${escapeHtml(gw.name)}')" title="Destroy">
                                        <i class="bi bi-trash3"></i>
                                    </button>
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
            ${renderDiagnostics(diag)}
        `;
    } catch (e) {
        container.innerHTML = `<div class="alert alert-danger">${escapeHtml(e.message)}</div>`;
    }
}

function renderDiagnostics(diag) {
    return `
        <div class="card" class="sg-card-themed">
            <div class="card-body">
                <h6 class="text-muted mb-3"><i class="bi bi-wrench me-2"></i>System Diagnostics</h6>
                <table class="table table-dark table-sm table-borderless mb-0">
                    <tr>
                        <td class="text-muted" style="width:130px">OpenShell CLI</td>
                        <td>
                            ${diag.openshell_installed
                                ? `<span class="badge text-bg-success">Installed</span> <span class="text-muted small ms-1">${escapeHtml(diag.openshell_version || '')}</span>`
                                : '<span class="badge text-bg-danger">Not found</span>'}
                        </td>
                    </tr>
                    <tr>
                        <td class="text-muted">Docker</td>
                        <td>
                            ${!diag.docker_installed
                                ? '<span class="badge text-bg-danger">Not installed</span>'
                                : !diag.docker_daemon_running
                                    ? '<span class="badge text-bg-danger">Daemon stopped</span>'
                                    : diag.docker_accessible
                                        ? `<span class="badge text-bg-success">Running</span> <span class="text-muted small ms-1">v${escapeHtml(diag.docker_version || '')}</span>`
                                        : '<span class="badge text-bg-warning">No access</span>'}
                        </td>
                    </tr>
                    ${diag.docker_error ? `
                    <tr>
                        <td class="text-muted">Error</td>
                        <td class="text-warning small">${escapeHtml(diag.docker_error)}</td>
                    </tr>` : ''}
                    <tr>
                        <td class="text-muted">Docker Group</td>
                        <td>
                            ${diag.in_docker_group
                                ? '<span class="badge text-bg-success">Member</span>'
                                : '<span class="badge text-bg-warning">Not a member</span>'}
                            <span class="text-muted small ms-1">(user: ${escapeHtml(diag.user)})</span>
                        </td>
                    </tr>
                </table>
            </div>
        </div>
    `;
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
                    <div class="btn-group btn-group-sm">
                        <button class="btn btn-success" onclick="gatewayAction('start', '${escapeHtml(name)}')" id="gw-start-btn"
                                ${gw.container_status === 'running' ? 'disabled' : ''}>
                            <i class="bi bi-play-fill me-1"></i>Start
                        </button>
                        <button class="btn btn-outline-warning" onclick="gatewayAction('stop', '${escapeHtml(name)}')" id="gw-stop-btn"
                                ${gw.container_status !== 'running' ? 'disabled' : ''}>
                            <i class="bi bi-stop-fill me-1"></i>Stop
                        </button>
                        <button class="btn btn-outline-secondary" onclick="gatewayAction('restart', '${escapeHtml(name)}')" id="gw-restart-btn">
                            <i class="bi bi-arrow-repeat me-1"></i>Restart
                        </button>
                    </div>
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
                <dt class="col-sm-3 text-muted fw-normal">Type</dt>
                <dd class="col-sm-9">${renderGatewayTypeIcon(gw.type)}</dd>
                ${gw.endpoint ? `
                <dt class="col-sm-3 text-muted fw-normal">Endpoint</dt>
                <dd class="col-sm-9 font-monospace small">${escapeHtml(gw.endpoint)}</dd>` : ''}
                ${gw.port ? `
                <dt class="col-sm-3 text-muted fw-normal">Port</dt>
                <dd class="col-sm-9">${gw.port}</dd>` : ''}
                ${gw.remote_host ? `
                <dt class="col-sm-3 text-muted fw-normal">Remote Host</dt>
                <dd class="col-sm-9 font-monospace small">${escapeHtml(gw.remote_host)}</dd>` : ''}
                ${gw.auth_mode ? `
                <dt class="col-sm-3 text-muted fw-normal">Auth</dt>
                <dd class="col-sm-9">${escapeHtml(gw.auth_mode)}</dd>` : ''}
            </dl>

            <div class="border-top pt-3" style="border-color:var(--sg-border)!important">
                <button class="btn btn-outline-danger btn-sm" onclick="destroyGateway('${escapeHtml(name)}')">
                    <i class="bi bi-trash me-1"></i>Destroy Gateway
                </button>
            </div>

            <!-- Inference Provider (only for active gateway) -->
            ${gw.active ? `
            <div class="card mb-4" class="sg-card-themed">
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

const _actionLabels = {
    start: 'Starting',
    stop: 'Stopping',
    restart: 'Restarting',
};

async function gatewayAction(action, name) {
    const output = document.getElementById('gw-action-output');
    const startBtn = document.getElementById('gw-start-btn');
    const stopBtn = document.getElementById('gw-stop-btn');
    const restartBtn = document.getElementById('gw-restart-btn');

    if (startBtn) startBtn.disabled = true;
    if (stopBtn) stopBtn.disabled = true;
    if (restartBtn) restartBtn.disabled = true;

    const label = _actionLabels[action] || action;
    output.innerHTML = `
        <div class="log-output small">
            <div class="log-line"><div class="spinner-border spinner-border-sm me-2"></div>${label} gateway... This may take a minute.</div>
        </div>`;

    try {
        const url = name ? `${API_GLOBAL}/gateway/${name}/${action}` : `${API_GLOBAL}/gateway/${action}`;
        const result = await apiFetch(url, { method: 'POST' });

        if (result.success) {
            output.innerHTML = `
                <div class="log-output small">
                    <div class="log-line log-info">${label} gateway... done!</div>
                    ${result.output ? `<div class="log-line">${escapeHtml(result.output)}</div>` : ''}
                </div>`;
            showToast(`Gateway ${action}ed successfully.`, 'success');
            setTimeout(async () => {
                await checkGatewayHealth();
                if (name) loadGatewayDetail(name);
                else loadGatewayPage();
            }, SG.config.actionRefreshDelay);
        } else {
            output.innerHTML = `
                <div class="log-output small">
                    <div class="log-line log-error">${label} gateway... failed!</div>
                    <div class="log-line log-error">${escapeHtml(result.error || 'Unknown error')}</div>
                </div>`;
            showToast(`Gateway ${action} failed.`, 'danger');
            if (startBtn) startBtn.disabled = false;
            if (stopBtn) stopBtn.disabled = false;
            if (restartBtn) restartBtn.disabled = false;
        }
    } catch (e) {
        output.innerHTML = `
            <div class="log-output small">
                <div class="log-line log-error">Error: ${escapeHtml(e.message)}</div>
            </div>`;
        if (startBtn) startBtn.disabled = false;
        if (stopBtn) stopBtn.disabled = false;
        if (restartBtn) restartBtn.disabled = false;
    }
}

async function destroyGateway(name) {
    const confirmed = await showConfirm(
        `Destroy gateway "${name}"? This removes all state and certificates.`,
        { icon: 'trash', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Destroy' }
    );
    if (!confirmed) return;
    try {
        const result = await apiFetch(`${API_GLOBAL}/gateway/${name}/destroy`, { method: 'POST' });
        if (result.success) {
            showToast(`Gateway "${name}" destroyed.`, 'success');
            checkGatewayHealth();
            navigateTo('/gateways');
        } else {
            showToast(`Failed: ${result.error}`, 'danger');
        }
    } catch (e) {
        showToast(`Error: ${e.message}`, 'danger');
    }
}

async function createGateway(e) {
    if (e) e.preventDefault();

    const name = document.getElementById('new-gw-name').value.trim();
    const port = parseInt(document.getElementById('new-gw-port').value) || 8080;
    const remote = document.getElementById('new-gw-remote').value.trim();
    const gpu = document.getElementById('new-gw-gpu').checked;
    const output = document.getElementById('create-gw-output');
    const btn = document.getElementById('create-gw-btn');

    if (!name) {
        output.innerHTML = '<div class="text-danger small">Name is required.</div>';
        return;
    }

    btn.disabled = true;
    output.innerHTML = '<div class="text-muted small"><div class="spinner-border spinner-border-sm me-2"></div>Creating gateway... This may take a few minutes.</div>';

    try {
        const result = await apiFetch(`${API_GLOBAL}/gateway/create`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, port, remote_host: remote || null, gpu }),
        });

        if (result.success) {
            output.innerHTML = '<div class="text-success small"><i class="bi bi-check-circle me-1"></i>Gateway created!</div>';
            showToast(`Gateway "${name}" created.`, 'success');
            bootstrap.Modal.getInstance(document.getElementById('createGatewayModal'))?.hide();
            checkGatewayHealth();
            loadGatewayPage();
        } else {
            output.innerHTML = `<div class="text-danger small"><i class="bi bi-x-circle me-1"></i>${escapeHtml(result.error)}</div>`;
        }
    } catch (e) {
        output.innerHTML = `<div class="text-danger small">${escapeHtml(e.message)}</div>`;
    } finally {
        btn.disabled = false;
    }
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
            ${currentProvider ? `<code>${currentProvider.envVar}</code>` : ''}
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
