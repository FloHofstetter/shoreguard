/**
 * Shoreguard — Network Rule Detail & Editor
 * Dedicated page for viewing and editing a single network rule.
 */

let _ruleEditorEndpointCounter = 0;

async function loadRuleDetail(sandboxName, ruleKey) {
    const container = document.getElementById('rule-content');
    container.innerHTML = renderSpinner('Loading rule...');

    const isNew = ruleKey === '_new';

    if (isNew) {
        const titleEl = document.getElementById('rule-title');
        if (titleEl) titleEl.textContent = 'New Rule';
        container.innerHTML = renderRuleEditor(sandboxName, null, null);
        return;
    }

    try {
        const data = await apiFetch(`${API}/sandboxes/${sandboxName}/policy`);
        const policy = data.policy;

        if (!policy || !policy.network_policies || !policy.network_policies[ruleKey]) {
            container.innerHTML = renderError(`Rule "${ruleKey}" not found.`);
            return;
        }

        const rule = policy.network_policies[ruleKey];

        // Show delete button (may not exist in all template variants)
        const deleteBtn = document.getElementById('rule-delete-btn');
        if (deleteBtn) {
            deleteBtn.style.display = '';
            deleteBtn.onclick = () => deleteRule(sandboxName, ruleKey);
        }

        container.innerHTML = renderRuleView(sandboxName, ruleKey, rule);
    } catch (e) {
        container.innerHTML = renderError(e.message);
    }
}

function renderRuleView(sandboxName, ruleKey, rule) {
    const endpoints = rule.endpoints || [];
    const binaries = rule.binaries || [];

    return `
        <div class="d-flex justify-content-between align-items-center mb-4">
            <div>
                <h4 class="mb-1">${escapeHtml(rule.name || ruleKey)}</h4>
                <span class="text-muted small">Key: <code>${escapeHtml(ruleKey)}</code></span>
            </div>
            <button class="btn btn-outline-primary btn-sm" onclick="switchToEdit('${escapeHtml(sandboxName)}', '${escapeHtml(ruleKey)}')">
                <i class="bi bi-pencil me-1"></i>Edit
            </button>
        </div>

        <!-- Endpoints -->
        <h6 class="text-muted mb-2"><i class="bi bi-globe me-1"></i>Endpoints <span class="badge text-bg-secondary ms-1">${endpoints.length}</span></h6>
        ${endpoints.length > 0 ? `
            <div class="table-responsive mb-4">
                <table class="table table-dark table-striped table-sm align-middle mb-0">
                    <thead>
                        <tr>
                            <th>Host</th>
                            <th>Port</th>
                            <th>Protocol</th>
                            <th>TLS</th>
                            <th>Enforcement</th>
                            <th>Access</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${endpoints.map(ep => `
                            <tr>
                                <td class="font-monospace">${escapeHtml(ep.host)}</td>
                                <td>${ep.port || 443}</td>
                                <td>${ep.protocol ? `<span class="badge text-bg-secondary">${escapeHtml(ep.protocol)}</span>` : '<span class="text-muted">—</span>'}</td>
                                <td>${ep.tls ? `<span class="badge text-bg-info">${escapeHtml(ep.tls)}</span>` : '<span class="text-muted">—</span>'}</td>
                                <td>${ep.enforcement ? `<span class="badge text-bg-warning">${escapeHtml(ep.enforcement)}</span>` : '<span class="text-muted">—</span>'}</td>
                                <td>${ep.access ? `<span class="badge text-bg-success">${escapeHtml(ep.access)}</span>` : '<span class="text-muted">—</span>'}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        ` : '<p class="text-muted mb-4">No endpoints configured.</p>'}

        <!-- Binaries -->
        <h6 class="text-muted mb-2"><i class="bi bi-terminal me-1"></i>Allowed Binaries <span class="badge text-bg-secondary ms-1">${binaries.length}</span></h6>
        ${binaries.length > 0 ? `
            <div class="table-responsive">
                <table class="table table-dark table-striped table-sm align-middle mb-0">
                    <thead>
                        <tr><th>Path</th></tr>
                    </thead>
                    <tbody>
                        ${binaries.map(b => `
                            <tr><td class="font-monospace small">${escapeHtml(b.path)}</td></tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        ` : '<p class="text-muted">No binary restrictions.</p>'}
    `;
}

async function switchToEdit(sandboxName, ruleKey) {
    const container = document.getElementById('rule-content');
    const data = await apiFetch(`${API}/sandboxes/${sandboxName}/policy`);
    const rule = data.policy?.network_policies?.[ruleKey] || null;
    container.innerHTML = renderRuleEditor(sandboxName, ruleKey, rule);
}

function renderRuleEditor(sandboxName, ruleKey, existing) {
    const isNew = !existing;
    _ruleEditorEndpointCounter = 0;

    const endpoints = existing?.endpoints || [];
    const binaries = existing?.binaries || [];

    let html = `
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h4 class="mb-0">${isNew ? 'New Network Rule' : `Edit: ${escapeHtml(existing.name || ruleKey)}`}</h4>
            ${!isNew ? `<button class="btn btn-outline-secondary btn-sm" onclick="loadRuleDetail('${escapeHtml(sandboxName)}', '${escapeHtml(ruleKey)}')">
                <i class="bi bi-x me-1"></i>Cancel
            </button>` : ''}
        </div>

        <div class="row g-3 mb-4">
            <div class="col-md-6">
                <label class="form-label">Rule Key</label>
                <input type="text" id="edit-rule-key" class="form-control form-control-sm" value="${escapeHtml(ruleKey || '')}"
                       placeholder="e.g. allow-pypi" ${!isNew ? 'readonly' : ''}>
                <div class="form-text">Unique identifier for this rule.</div>
            </div>
            <div class="col-md-6">
                <label class="form-label">Display Name</label>
                <input type="text" id="edit-rule-name" class="form-control form-control-sm" value="${escapeHtml(existing?.name || '')}"
                       placeholder="e.g. PyPI Package Registry">
            </div>
        </div>

        <!-- Endpoints -->
        <div class="d-flex justify-content-between align-items-center mb-2">
            <h6 class="mb-0"><i class="bi bi-globe me-1"></i>Endpoints</h6>
            <button class="btn btn-outline-success btn-sm" type="button" onclick="addRuleEndpoint()">
                <i class="bi bi-plus me-1"></i>Add Endpoint
            </button>
        </div>
        <div id="rule-endpoints" class="mb-4"></div>

        <!-- Binaries -->
        <div class="mb-4">
            <h6><i class="bi bi-terminal me-1"></i>Allowed Binaries</h6>
            <textarea id="edit-rule-binaries" class="form-control form-control-sm font-monospace" rows="4"
                      placeholder="One path per line, e.g.&#10;/usr/bin/curl&#10;/usr/bin/git">${binaries.map(b => b.path).join('\n')}</textarea>
            <div class="form-text">One binary path per line.</div>
        </div>

        <!-- Actions -->
        <div class="d-flex gap-2">
            <button class="btn btn-success" onclick="saveRule('${escapeHtml(sandboxName)}', ${isNew ? 'null' : `'${escapeHtml(ruleKey)}'`})">
                <i class="bi bi-check me-1"></i>Save Rule
            </button>
            ${isNew
                ? `<a href="${gwUrl('/sandboxes/' + sandboxName + '/policy')}" class="btn btn-outline-secondary">Cancel</a>`
                : `<button class="btn btn-outline-secondary" onclick="loadRuleDetail('${escapeHtml(sandboxName)}', '${escapeHtml(ruleKey)}')">Cancel</button>`
            }
        </div>
    `;

    // We need to render endpoints after the DOM is created
    setTimeout(() => {
        if (endpoints.length > 0) {
            endpoints.forEach(ep => addRuleEndpoint(ep));
        } else if (isNew) {
            addRuleEndpoint();
        }
    }, 0);

    return html;
}

function addRuleEndpoint(ep = {}) {
    const id = `rule-ep-${_ruleEditorEndpointCounter++}`;
    const container = document.getElementById('rule-endpoints');
    container.insertAdjacentHTML('beforeend', `
        <div class="card sg-overlay-card mb-2" id="${id}">
            <div class="card-body py-2 px-3">
                <div class="d-flex justify-content-end mb-2">
                    <button class="btn btn-outline-danger btn-sm" type="button" onclick="document.getElementById('${id}').remove()" title="Remove endpoint">
                        <i class="bi bi-x"></i>
                    </button>
                </div>
                <div class="row g-2 mb-2">
                    <div class="col-md-6">
                        <label class="form-label small text-muted mb-1">Host</label>
                        <input type="text" class="form-control form-control-sm ep-host" placeholder="api.example.com" value="${escapeHtml(ep.host || '')}">
                    </div>
                    <div class="col-md-2">
                        <label class="form-label small text-muted mb-1">Port</label>
                        <input type="number" class="form-control form-control-sm ep-port" placeholder="443" value="${ep.port || 443}">
                    </div>
                    <div class="col-md-4">
                        <label class="form-label small text-muted mb-1">Protocol</label>
                        <select class="form-select form-select-sm ep-protocol">
                            <option value="">—</option>
                            <option value="rest" ${ep.protocol === 'rest' ? 'selected' : ''}>REST</option>
                            <option value="sql" ${ep.protocol === 'sql' ? 'selected' : ''}>SQL</option>
                        </select>
                    </div>
                </div>
                <div class="row g-2">
                    <div class="col-md-4">
                        <label class="form-label small text-muted mb-1">TLS</label>
                        <select class="form-select form-select-sm ep-tls">
                            <option value="">—</option>
                            <option value="terminate" ${ep.tls === 'terminate' ? 'selected' : ''}>Terminate</option>
                            <option value="passthrough" ${ep.tls === 'passthrough' ? 'selected' : ''}>Passthrough</option>
                        </select>
                    </div>
                    <div class="col-md-4">
                        <label class="form-label small text-muted mb-1">Enforcement</label>
                        <select class="form-select form-select-sm ep-enforcement">
                            <option value="">—</option>
                            <option value="enforce" ${ep.enforcement === 'enforce' ? 'selected' : ''}>Enforce</option>
                            <option value="audit" ${ep.enforcement === 'audit' ? 'selected' : ''}>Audit</option>
                        </select>
                    </div>
                    <div class="col-md-4">
                        <label class="form-label small text-muted mb-1">Access</label>
                        <select class="form-select form-select-sm ep-access">
                            <option value="">—</option>
                            <option value="full" ${ep.access === 'full' ? 'selected' : ''}>Full</option>
                        </select>
                    </div>
                </div>
            </div>
        </div>`);
}

function collectRuleEndpoints() {
    return [...document.querySelectorAll('#rule-endpoints .card')].map(card => {
        const ep = {
            host: card.querySelector('.ep-host')?.value?.trim() || '',
            port: parseInt(card.querySelector('.ep-port')?.value) || 443,
        };
        const protocol = card.querySelector('.ep-protocol')?.value;
        const tls = card.querySelector('.ep-tls')?.value;
        const enforcement = card.querySelector('.ep-enforcement')?.value;
        const access = card.querySelector('.ep-access')?.value;
        if (protocol) ep.protocol = protocol;
        if (tls) ep.tls = tls;
        if (enforcement) ep.enforcement = enforcement;
        if (access) ep.access = access;
        return ep;
    }).filter(ep => ep.host);
}

async function saveRule(sandboxName, existingKey) {
    const key = existingKey || document.getElementById('edit-rule-key').value.trim();
    const name = document.getElementById('edit-rule-name').value.trim();
    const endpoints = collectRuleEndpoints();
    const binariesRaw = document.getElementById('edit-rule-binaries').value.trim();
    const binaries = binariesRaw ? binariesRaw.split('\n').map(p => ({ path: p.trim() })).filter(b => b.path) : [];

    if (!key) { showToast('Rule key is required.', 'warning'); return; }
    if (endpoints.length === 0) { showToast('At least one endpoint is required.', 'warning'); return; }

    try {
        await apiFetch(`${API}/sandboxes/${sandboxName}/policy/network-rules`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key, rule: { name: name || key, endpoints, binaries } }),
        });

        showToast(`Rule "${key}" saved.`, 'success');
        navigateTo(gwUrl(`/sandboxes/${sandboxName}/rules/${key}`));
    } catch (e) {
        showToast(`Failed to save: ${e.message}`, 'danger');
    }
}

async function deleteRule(sandboxName, ruleKey) {
    const confirmed = await showConfirm(
        `Delete network rule "${ruleKey}"?`,
        { icon: 'trash', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Delete' }
    );
    if (!confirmed) return;

    try {
        await apiFetch(`${API}/sandboxes/${sandboxName}/policy/network-rules/${ruleKey}`, {
            method: 'DELETE',
        });
        showToast(`Rule "${ruleKey}" deleted.`, 'success');
        navigateTo(gwUrl(`/sandboxes/${sandboxName}/policy`));
    } catch (e) {
        showToast(`Failed to delete: ${e.message}`, 'danger');
    }
}
