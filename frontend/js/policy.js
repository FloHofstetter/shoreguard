/**
 * Shoreguard — Policy Tab & Presets Page
 * Card-based policy overview, table-based presets listing.
 */

let _policyContext = { sandboxName: null, policy: null };

// ─── Policy Tab (Overview) ──────────────────────────────────────────────────

async function loadPolicyTab(name, container) {
    container.innerHTML = renderSpinner('Loading policy...');
    try {
        const data = await apiFetch(`${API}/sandboxes/${name}/policy`);
        const policy = data.policy;

        if (!policy) {
            container.innerHTML = '<div class="text-muted py-4"><i class="bi bi-info-circle me-1"></i>No policy data available.</div>';
            return;
        }

        _policyContext = { sandboxName: name, policy };
        const networkRules = policy.network_policies || {};
        const networkCount = Object.keys(networkRules).length;
        const fsRows = countFilesystemPaths(policy.filesystem);
        const procRows = countProcessRows(policy);

        container.innerHTML = `
            <div class="mb-4">
                <span class="text-muted">Policy v${policy.version || '?'}</span>
            </div>

            <div class="row g-3 mb-4">
                <div class="col-md-4">
                    <a href="${gwUrl('/sandboxes/' + name + '/network-policies')}" class="card text-decoration-none policy-overview-card sg-card-themed h-100">
                        <div class="card-body">
                            <div class="d-flex align-items-center mb-2">
                                <i class="bi bi-globe text-info me-2"></i>
                                <h6 class="mb-0">Network Policies</h6>
                            </div>
                            <div class="fs-2 fw-bold mb-1">${networkCount}</div>
                            <span class="text-muted small">${networkCount === 1 ? '1 rule' : networkCount + ' rules'} configured</span>
                        </div>
                        <div class="card-footer border-0 pt-0 small" style="background:transparent">
                            Manage rules <i class="bi bi-arrow-right"></i>
                        </div>
                    </a>
                </div>
                <div class="col-md-4">
                    <a href="${gwUrl('/sandboxes/' + name + '/filesystem-policy')}" class="card text-decoration-none policy-overview-card sg-card-themed h-100">
                        <div class="card-body">
                            <div class="d-flex align-items-center mb-2">
                                <i class="bi bi-folder text-warning me-2"></i>
                                <h6 class="mb-0">Filesystem</h6>
                            </div>
                            <div class="fs-2 fw-bold mb-1">${fsRows}</div>
                            <span class="text-muted small">${fsRows === 1 ? '1 path' : fsRows + ' paths'} configured</span>
                        </div>
                        <div class="card-footer border-0 pt-0 small" style="background:transparent">
                            View paths <i class="bi bi-arrow-right"></i>
                        </div>
                    </a>
                </div>
                <div class="col-md-4">
                    <a href="${gwUrl('/sandboxes/' + name + '/process-policy')}" class="card text-decoration-none policy-overview-card sg-card-themed h-100">
                        <div class="card-body">
                            <div class="d-flex align-items-center mb-2">
                                <i class="bi bi-cpu text-success me-2"></i>
                                <h6 class="mb-0">Process & Landlock</h6>
                            </div>
                            <div class="fs-2 fw-bold mb-1">${procRows}</div>
                            <span class="text-muted small">${procRows === 1 ? '1 setting' : procRows + ' settings'} configured</span>
                        </div>
                        <div class="card-footer border-0 pt-0 small" style="background:transparent">
                            View settings <i class="bi bi-arrow-right"></i>
                        </div>
                    </a>
                </div>
            </div>

            <!-- Presets -->
            <div class="row g-3 mt-0">
                <div class="col-12">
                    <a href="${gwUrl('/sandboxes/' + name + '/apply-preset')}" class="card text-decoration-none policy-overview-card sg-card-themed">
                        <div class="card-body d-flex align-items-center">
                            <div class="me-3">
                                <i class="bi bi-shield-plus fs-4 text-info"></i>
                            </div>
                            <div class="flex-grow-1">
                                <h6 class="mb-0">Apply Preset</h6>
                                <span class="text-muted small">Add predefined network rules from a template</span>
                            </div>
                            <i class="bi bi-arrow-right"></i>
                        </div>
                    </a>
                </div>
            </div>`;

    } catch (e) {
        container.innerHTML = `<div class="alert alert-danger">${escapeHtml(e.message)}</div>`;
    }
}

function countFilesystemPaths(fs) {
    if (!fs) return 0;
    return (fs.read_only || []).length + (fs.read_write || []).length;
}

function countProcessRows(policy) {
    let count = 0;
    if (policy.process) {
        if (policy.process.run_as_user) count++;
        if (policy.process.run_as_group) count++;
    }
    if (policy.landlock) {
        if (policy.landlock.compatibility) count++;
    }
    return count;
}

// ─── Network Policies Page ──────────────────────────────────────────────────

async function loadNetworkPolicies(name) {
    const container = document.getElementById('policy-page-content');
    container.innerHTML = renderSpinner('Loading network policies...');
    try {
        const data = await apiFetch(`${API}/sandboxes/${name}/policy`);
        const policy = data.policy;
        const networkRules = policy?.network_policies || {};
        const networkEntries = Object.entries(networkRules);

        container.innerHTML = `
            <div class="d-flex justify-content-end mb-3">
                <a href="${gwUrl('/sandboxes/' + name + '/rules/_new')}" class="btn btn-outline-success btn-sm">
                    <i class="bi bi-plus me-1"></i>Add Rule
                </a>
            </div>

            ${networkEntries.length > 0 ? `
                <div class="table-responsive">
                    <table class="table table-dark table-striped table-hover table-sm align-middle table-clickable">
                        <thead>
                            <tr>
                                <th>Rule</th>
                                <th>Endpoints</th>
                                <th>Binaries</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${networkEntries.map(([key, rule]) => renderNetworkRuleRow(name, key, rule)).join('')}
                        </tbody>
                    </table>
                </div>
            ` : renderEmptyState('globe', 'No network policies configured.',
                `<a href="${gwUrl('/sandboxes/' + name + '/rules/_new')}" class="btn btn-outline-success btn-sm"><i class="bi bi-plus me-1"></i>Add Rule</a>`
            )}`;
    } catch (e) {
        container.innerHTML = renderError(e.message);
    }
}

function renderNetworkRuleRow(sandboxName, key, rule) {
    const endpoints = rule.endpoints || [];
    const binaries = rule.binaries || [];
    const topHosts = endpoints.slice(0, 2).map(ep => ep.host).join(', ');
    const moreCount = endpoints.length > 2 ? ` +${endpoints.length - 2}` : '';
    const normalizedName = (rule.name || '').replace(/-/g, '_');
    const showKey = key !== rule.name && key !== normalizedName;

    return `
        <tr onclick="navigateTo(gwUrl('/sandboxes/${sandboxName}/rules/${encodeURIComponent(key)}'))">
            <td>
                <strong>${escapeHtml(rule.name || key)}</strong>
                ${showKey ? `<div class="text-muted small">${escapeHtml(key)}</div>` : ''}
            </td>
            <td>
                <span class="text-muted small font-monospace">${escapeHtml(topHosts)}${moreCount}</span>
            </td>
            <td>
                <span class="badge text-bg-secondary">${binaries.length}</span>
            </td>
        </tr>`;
}

// ─── Filesystem Policy Page ─────────────────────────────────────────────────

async function loadFilesystemPolicy(name) {
    const container = document.getElementById('policy-page-content');
    container.innerHTML = renderSpinner('Loading filesystem policy...');
    try {
        const data = await apiFetch(`${API}/sandboxes/${name}/policy`);
        const fs = data.policy?.filesystem;

        const rows = [];
        if (fs) {
            for (const path of (fs.read_only || [])) {
                rows.push({ path, access: 'ro', label: 'Read Only', badge: 'text-bg-warning' });
            }
            for (const path of (fs.read_write || [])) {
                rows.push({ path, access: 'rw', label: 'Read / Write', badge: 'text-bg-success' });
            }
        }

        container.innerHTML = `
            <div class="d-flex justify-content-between align-items-center mb-3">
                <span class="text-muted small"><i class="bi bi-info-circle me-1"></i>Existing paths cannot be removed or changed. New paths can be added.</span>
                <button class="btn btn-outline-success btn-sm" onclick="showAddFilesystemPath('${name}')">
                    <i class="bi bi-plus me-1"></i>Add Path
                </button>
            </div>

            <div id="fs-add-form" style="display:none" class="card sg-card-themed mb-3">
                <div class="card-body sg-overlay-card">
                    <div class="row g-2 align-items-end">
                        <div class="col-md-6">
                            <label class="form-label small">Path</label>
                            <input type="text" id="fs-new-path" class="form-control form-control-sm font-monospace" placeholder="/usr/local/bin">
                        </div>
                        <div class="col-md-3">
                            <label class="form-label small">Access</label>
                            <select id="fs-new-access" class="form-select form-select-sm">
                                <option value="ro">Read Only</option>
                                <option value="rw">Read / Write</option>
                            </select>
                        </div>
                        <div class="col-md-3 d-flex gap-2">
                            <button class="btn btn-success btn-sm" onclick="addFilesystemPath('${name}')">
                                <i class="bi bi-check me-1"></i>Add
                            </button>
                            <button class="btn btn-outline-secondary btn-sm" onclick="document.getElementById('fs-add-form').style.display='none'">
                                Cancel
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            ${rows.length > 0 ? `
                <div class="table-responsive">
                    <table class="table table-dark table-striped table-sm align-middle">
                        <thead>
                            <tr>
                                <th>Path</th>
                                <th style="width:120px">Access</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${rows.map(r => `
                                <tr>
                                    <td class="font-monospace small">${escapeHtml(r.path)}</td>
                                    <td><span class="badge ${r.badge}">${r.label}</span></td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            ` : renderEmptyState('folder', 'No filesystem paths configured.')}`;
    } catch (e) {
        container.innerHTML = renderError(e.message);
    }
}

function showAddFilesystemPath(name) {
    const form = document.getElementById('fs-add-form');
    form.style.display = '';
    document.getElementById('fs-new-path')?.focus();
}

async function addFilesystemPath(sandboxName) {
    const path = document.getElementById('fs-new-path').value.trim();
    const access = document.getElementById('fs-new-access').value;
    if (!path) { showToast('Path is required.', 'warning'); return; }

    try {
        await apiFetch(`${API}/sandboxes/${sandboxName}/policy/filesystem`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, access }),
        });
        showToast(`Path "${path}" added.`, 'success');
        loadFilesystemPolicy(sandboxName);
    } catch (e) {
        showToast(`Failed: ${e.message}`, 'danger');
    }
}


// ─── Process Policy Page ────────────────────────────────────────────────────

async function loadProcessPolicy(name) {
    const container = document.getElementById('policy-page-content');
    container.innerHTML = renderSpinner('Loading process policy...');
    try {
        const data = await apiFetch(`${API}/sandboxes/${name}/policy`);
        const policy = data.policy || {};

        const runAsUser = policy.process?.run_as_user || '';
        const runAsGroup = policy.process?.run_as_group || '';
        const landlockCompat = policy.landlock?.compatibility || '';

        container.innerHTML = `
            <p class="text-muted small mb-3"><i class="bi bi-lock me-1"></i>Process and Landlock settings are immutable after sandbox creation.</p>

            <div class="table-responsive">
                <table class="table table-dark table-sm align-middle">
                    <thead>
                        <tr>
                            <th>Setting</th>
                            <th>Value</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td>Run as user</td>
                            <td class="font-monospace">${escapeHtml(runAsUser) || '<span class="text-muted">—</span>'}</td>
                        </tr>
                        <tr>
                            <td>Run as group</td>
                            <td class="font-monospace">${escapeHtml(runAsGroup) || '<span class="text-muted">—</span>'}</td>
                        </tr>
                        <tr>
                            <td>Landlock compatibility</td>
                            <td class="font-monospace">${escapeHtml(landlockCompat) || '<span class="text-muted">—</span>'}</td>
                        </tr>
                    </tbody>
                </table>
            </div>`;
    } catch (e) {
        container.innerHTML = renderError(e.message);
    }
}



// ─── Apply Preset Page ───────────────────────────────────────────────────────

async function loadPresetsPage(sandboxName) {
    const container = document.getElementById('policy-page-content');
    container.innerHTML = renderSpinner('Loading presets...');
    try {
        const presets = await apiFetch(`${API_GLOBAL}/policies/presets`);

        if (!presets.length) {
            container.innerHTML = renderEmptyState('shield-plus', 'No presets available.');
            return;
        }

        container.innerHTML = `
            <div class="row g-3">
                ${presets.map(p => `
                    <div class="col-md-4">
                        <div class="card h-100 policy-overview-card sg-card-themed">
                            <div class="card-body">
                                <h6 class="mb-2">${escapeHtml(p.name)}</h6>
                                <p class="text-muted small mb-0">${escapeHtml(p.description || '')}</p>
                            </div>
                            <div class="card-footer border-0 pt-0" style="background:transparent">
                                <button class="btn btn-outline-success btn-sm" onclick="applyPreset('${sandboxName}', '${p.name}')">
                                    <i class="bi bi-plus me-1"></i>Apply
                                </button>
                            </div>
                        </div>
                    </div>
                `).join('')}
            </div>`;
    } catch (e) {
        container.innerHTML = renderError(e.message);
    }
}

async function applyPreset(sandboxName, presetName) {
    const confirmed = await showConfirm(
        `Apply "${presetName}" preset to ${sandboxName}?`,
        { icon: 'shield-plus', iconColor: 'text-success', btnClass: 'btn-success', btnLabel: 'Apply' }
    );
    if (!confirmed) return;
    try {
        await apiFetch(`${API}/sandboxes/${sandboxName}/policy/presets/${presetName}`, { method: 'POST' });
        showToast(`Preset "${presetName}" applied.`, 'success');
        navigateTo(gwUrl('/sandboxes/' + sandboxName + '/policy'));
    } catch (e) {
        showToast(`Failed to apply preset: ${e.message}`, 'danger');
    }
}

// ─── Policies Page ───────────────────────────────────────────────────────────

async function loadPresets() {
    const container = document.getElementById('presets-list');
    container.innerHTML = '<div class="text-center text-muted py-5"><div class="spinner-border spinner-border-sm me-2"></div>Loading presets...</div>';
    try {
        const presets = await apiFetch(`${API_GLOBAL}/policies/presets`);

        if (!presets.length) {
            container.innerHTML = '<div class="text-center text-muted py-5"><i class="bi bi-info-circle me-1"></i>No policy presets available.</div>';
            return;
        }

        container.innerHTML = `
            <div class="table-responsive">
                <table class="table table-dark table-striped table-hover table-sm align-middle table-clickable">
                    <thead>
                        <tr>
                            <th>Name</th>
                            <th>Description</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${presets.map(p => `
                            <tr onclick="navigateTo('/policies/${p.name}')">
                                <td><strong>${escapeHtml(p.name)}</strong></td>
                                <td class="text-muted">${escapeHtml(p.description)}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>`;
    } catch (e) {
        container.innerHTML = `<div class="text-center text-muted py-5"><i class="bi bi-exclamation-triangle text-warning me-1"></i>Could not load presets: ${escapeHtml(e.message)}</div>`;
    }
}

// ─── Preset Detail Page ──────────────────────────────────────────────────────

async function showPresetDetail(presetName) {
    const container = document.getElementById('preset-detail');
    container.innerHTML = '<div class="text-center text-muted py-5"><div class="spinner-border spinner-border-sm me-2"></div>Loading preset...</div>';
    try {
        const data = await apiFetch(`${API_GLOBAL}/policies/presets/${presetName}`);

        const meta = data.preset || {};
        const rules = data.network_policies || {};
        const ruleEntries = Object.entries(rules);

        container.innerHTML = `
            <div class="d-flex justify-content-between align-items-center mb-3">
                <div>
                    <h4 class="mb-1 d-inline me-2">${escapeHtml(meta.name || presetName)}</h4>
                </div>
            </div>

            ${meta.description ? `<p class="text-muted mb-3">${escapeHtml(meta.description)}</p>` : ''}

            <!-- Apply to Sandbox -->
            <div class="card sg-card-themed mb-4">
                <div class="card-body py-2">
                    <div class="d-flex align-items-center gap-2">
                        <span class="text-muted small">Apply to:</span>
                        <select id="preset-apply-sandbox" class="form-select form-select-sm" style="max-width:250px"
                                onchange="document.getElementById('preset-apply-btn').disabled = !this.value">
                            <option value="">Select a sandbox...</option>
                        </select>
                        <button class="btn btn-success btn-sm" onclick="applyPresetFromDetail('${presetName}')"
                                id="preset-apply-btn" disabled>
                            <i class="bi bi-shield-plus me-1"></i>Apply
                        </button>
                    </div>
                </div>
            </div>

            <h6 class="text-muted mb-2">Network Rules <span class="badge text-bg-secondary ms-1">${ruleEntries.length}</span></h6>

            ${ruleEntries.length > 0 ? `
                <div class="table-responsive">
                    <table class="table table-dark table-striped table-sm align-middle">
                        <thead>
                            <tr>
                                <th>Rule</th>
                                <th>Host</th>
                                <th>Port</th>
                                <th>Protocol</th>
                                <th>TLS</th>
                                <th>L7 Rules</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${ruleEntries.flatMap(([key, rule]) =>
                                (rule.endpoints || []).map((ep, i) => `
                                    <tr>
                                        ${i === 0 ? `<td rowspan="${rule.endpoints.length}"><strong>${escapeHtml(rule.name || key)}</strong></td>` : ''}
                                        <td class="font-monospace">${escapeHtml(ep.host)}</td>
                                        <td>${ep.port}</td>
                                        <td>${ep.protocol ? `<span class="badge text-bg-secondary">${ep.protocol}</span>` : '<span class="text-muted">—</span>'}</td>
                                        <td>${ep.tls ? `<span class="badge text-bg-info">${ep.tls}</span>` : '<span class="text-muted">—</span>'}</td>
                                        <td>${(ep.rules || []).map(r =>
                                            `<span class="badge endpoint-badge me-1">${r.allow?.method || '*'} ${r.allow?.path || '/*'}</span>`
                                        ).join('') || '<span class="text-muted">—</span>'}</td>
                                    </tr>
                                `)
                            ).join('')}
                        </tbody>
                    </table>
                </div>
            ` : '<div class="text-muted">No network rules defined.</div>'}
        `;

        // Populate sandbox dropdown
        loadPresetSandboxDropdown();
    } catch (e) {
        container.innerHTML = `<div class="alert alert-danger">${escapeHtml(e.message)}</div>`;
    }
}

async function loadPresetSandboxDropdown() {
    const select = document.getElementById('preset-apply-sandbox');
    if (!select) return;
    try {
        const sandboxes = await apiFetch(`${API}/sandboxes`);
        const ready = sandboxes.filter(sb => sb.phase === 'ready');
        if (ready.length === 0) {
            select.innerHTML = '<option value="" disabled>No sandboxes available</option>';
            return;
        }
        select.innerHTML = '<option value="">Select a sandbox...</option>' +
            ready.map(sb => `<option value="${sb.name}">${escapeHtml(sb.name)}</option>`).join('');
    } catch {
        select.innerHTML = '<option value="" disabled>Could not load sandboxes</option>';
    }
}

async function applyPresetFromDetail(presetName) {
    const sandboxName = document.getElementById('preset-apply-sandbox')?.value;
    if (!sandboxName) return;
    await applyPreset(sandboxName, presetName);
}
