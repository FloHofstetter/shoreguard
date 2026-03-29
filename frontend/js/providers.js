/**
 * Shoreguard — Provider Management
 * List, create, update, delete credential providers.
 */

// Provider types loaded from API, cached after first fetch
let _providerTypes = {};
let _providerTypesLoaded = false;
// Cached provider list for edit lookups
let _providerCache = [];

async function _ensureProviderTypes() {
    if (_providerTypesLoaded) return;
    try {
        const types = await apiFetch(`${API}/providers/types`);
        for (const t of types) {
            _providerTypes[t.type] = t;
        }
        _providerTypesLoaded = true;
    } catch {}
}

function _getProviderIcon(type) {
    const info = _providerTypes[type];
    const icon = info?.icon || 'gear';
    return `<i class="bi bi-${icon} me-1"></i>`;
}

function _getProviderCredKey(type) {
    const info = _providerTypes[type];
    return info?.cred_key || 'API_KEY';
}

// ─── Providers List Page ────────────────────────────────────────────────────

async function loadProvidersPage() {
    const container = document.getElementById('providers-page-content');
    container.innerHTML = renderSpinner('Loading providers...');
    await _ensureProviderTypes();

    try {
        const providers = await apiFetch(`${API}/providers`);
        _providerCache = providers;

        if (providers.length === 0) {
            container.innerHTML = `
                <div class="text-center text-muted py-5">
                    <i class="bi bi-key fs-1 d-block mb-3"></i>
                    <p>No providers configured.</p>
                    <button class="btn btn-success btn-sm" data-bs-toggle="modal" data-bs-target="#createProviderModal">
                        <i class="bi bi-plus me-1"></i>Create Provider
                    </button>
                </div>`;
            return;
        }

        container.innerHTML = `
            <div class="table-responsive">
                <table class="table table-dark table-striped table-hover table-sm align-middle">
                    <thead>
                        <tr>
                            <th>Name</th>
                            <th>Type</th>
                            <th class="d-none d-md-table-cell">Credentials</th>
                            <th class="d-none d-md-table-cell">Config</th>
                            <th class="text-end">Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${providers.map(p => renderProviderRow(p)).join('')}
                    </tbody>
                </table>
            </div>`;
    } catch (e) {
        container.innerHTML = `<div class="alert alert-danger">${escapeHtml(e.message)}</div>`;
    }
}

function renderProviderRow(provider) {
    const icon = _getProviderIcon(provider.type);
    const credKeys = Object.keys(provider.credentials || {});
    const configKeys = Object.keys(provider.config || {});
    // Gateway redacts credential values — show the expected key name instead
    const expectedKey = _getProviderCredKey(provider.type);
    const maskedCreds = credKeys.length > 0
        ? credKeys.map(k => `${escapeHtml(k)}=***`).join(', ')
        : `<span class="text-muted"><i class="bi bi-lock-fill me-1"></i>${escapeHtml(expectedKey)} (redacted)</span>`;
    const configDisplay = configKeys.length > 0
        ? configKeys.map(k => `${escapeHtml(k)}=${escapeHtml(provider.config[k])}`).join(', ')
        : '<span class="text-muted">—</span>';

    return `
        <tr>
            <td><strong>${escapeHtml(provider.name)}</strong></td>
            <td>${icon}<span class="badge text-bg-secondary">${escapeHtml(provider.type)}</span></td>
            <td class="d-none d-md-table-cell small font-monospace">${maskedCreds}</td>
            <td class="d-none d-md-table-cell small font-monospace">${configDisplay}</td>
            <td class="text-end">
                <button class="btn btn-sm text-muted" onclick="editProvider('${escapeHtml(provider.name)}')" title="Edit">
                    <i class="bi bi-pencil"></i>
                </button>
                <button class="btn btn-sm text-muted delete-btn" onclick="deleteProvider('${escapeHtml(provider.name)}')" title="Delete">
                    <i class="bi bi-trash3"></i>
                </button>
            </td>
        </tr>`;
}

// ─── Provider Actions ────────────────────────────────────────────────────────

function _parseKeyValueLines(text) {
    const result = {};
    if (!text?.trim()) return result;
    for (const line of text.split('\n')) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        const idx = trimmed.indexOf('=');
        if (idx > 0) {
            result[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim();
        }
    }
    return result;
}

function onProviderTypeChange() {
    const type = document.getElementById('new-prov-type').value;
    const label = document.getElementById('new-prov-cred-label');
    const keyName = _getProviderCredKey(type);
    if (label) label.textContent = keyName;
}

async function createProvider(e) {
    if (e) e.preventDefault();

    const name = document.getElementById('new-prov-name').value.trim();
    const type = document.getElementById('new-prov-type').value;
    const apiKey = document.getElementById('new-prov-apikey').value.trim();
    const credsText = document.getElementById('new-prov-creds').value;
    const configText = document.getElementById('new-prov-config').value;
    const output = document.getElementById('create-prov-output');
    const btn = document.getElementById('create-prov-btn');

    if (!name) { output.innerHTML = '<div class="text-danger small">Name is required.</div>'; return; }
    if (!type) { output.innerHTML = '<div class="text-danger small">Type is required.</div>'; return; }
    if (!apiKey) { output.innerHTML = '<div class="text-danger small">API key is required.</div>'; return; }

    const extraCreds = _parseKeyValueLines(credsText);
    const config = _parseKeyValueLines(configText);

    btn.disabled = true;
    output.innerHTML = '<div class="text-muted small"><div class="spinner-border spinner-border-sm me-2"></div>Creating...</div>';

    try {
        await apiFetch(`${API}/providers`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name, type, api_key: apiKey,
                credentials: Object.keys(extraCreds).length > 0 ? extraCreds : undefined,
                config: Object.keys(config).length > 0 ? config : undefined,
            }),
        });
        output.innerHTML = '<div class="text-success small"><i class="bi bi-check-circle me-1"></i>Provider created!</div>';
        showToast(`Provider "${name}" created.`, 'success');
        bootstrap.Modal.getInstance(document.getElementById('createProviderModal'))?.hide();
        loadProvidersPage();
    } catch (e) {
        output.innerHTML = `<div class="text-danger small"><i class="bi bi-x-circle me-1"></i>${escapeHtml(e.message)}</div>`;
    } finally {
        btn.disabled = false;
    }
}

function editProvider(name) {
    const provider = _providerCache.find(p => p.name === name);
    if (!provider) return;

    // Reuse the create modal for editing
    document.getElementById('new-prov-name').value = provider.name;
    document.getElementById('new-prov-name').disabled = true;
    document.getElementById('new-prov-type').value = provider.type;
    document.getElementById('new-prov-type').disabled = true;
    onProviderTypeChange();
    document.getElementById('new-prov-apikey').value = '';
    document.getElementById('new-prov-apikey').placeholder = '(leave blank to keep current)';
    document.getElementById('new-prov-apikey').required = false;

    const configObj = provider.config || {};
    document.getElementById('new-prov-config').value =
        Object.entries(configObj).map(([k, v]) => `${k}=${v}`).join('\n');
    document.getElementById('new-prov-creds').value = '';

    const title = document.querySelector('#createProviderModal .modal-title');
    const btn = document.getElementById('create-prov-btn');
    title.innerHTML = '<i class="bi bi-pencil me-2"></i>Edit Provider';
    btn.innerHTML = '<i class="bi bi-check me-1"></i>Save';
    btn.setAttribute('onclick', `updateProvider(event, '${escapeHtml(name)}')`);
    document.getElementById('create-prov-output').innerHTML = '';

    new bootstrap.Modal(document.getElementById('createProviderModal')).show();
}

function _resetProviderModal() {
    document.getElementById('new-prov-name').disabled = false;
    document.getElementById('new-prov-type').disabled = false;
    document.getElementById('new-prov-apikey').placeholder = 'sk-...';
    document.getElementById('new-prov-apikey').required = true;
    const title = document.querySelector('#createProviderModal .modal-title');
    const btn = document.getElementById('create-prov-btn');
    title.innerHTML = '<i class="bi bi-key me-2"></i>New Provider';
    btn.innerHTML = '<i class="bi bi-plus me-1"></i>Create';
    btn.setAttribute('onclick', 'createProvider(event)');
    document.getElementById('create-provider-form').reset();
    document.getElementById('create-prov-output').innerHTML = '';
}

async function updateProvider(e, name) {
    if (e) e.preventDefault();

    const apiKey = document.getElementById('new-prov-apikey').value.trim();
    const configText = document.getElementById('new-prov-config').value;
    const credsText = document.getElementById('new-prov-creds').value;
    const output = document.getElementById('create-prov-output');
    const btn = document.getElementById('create-prov-btn');

    const config = _parseKeyValueLines(configText);
    const extraCreds = _parseKeyValueLines(credsText);

    const body = {
        type: document.getElementById('new-prov-type').value,
        config: Object.keys(config).length > 0 ? config : {},
    };
    // Only send credentials if user entered something
    if (apiKey || Object.keys(extraCreds).length > 0) {
        const keyName = _getProviderCredKey(body.type);
        body.credentials = { ...extraCreds };
        if (apiKey) body.credentials[keyName] = apiKey;
    }

    btn.disabled = true;
    output.innerHTML = '<div class="text-muted small"><div class="spinner-border spinner-border-sm me-2"></div>Saving...</div>';

    try {
        await apiFetch(`${API}/providers/${name}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        output.innerHTML = '<div class="text-success small"><i class="bi bi-check-circle me-1"></i>Provider updated!</div>';
        showToast(`Provider "${name}" updated.`, 'success');
        bootstrap.Modal.getInstance(document.getElementById('createProviderModal'))?.hide();
        loadProvidersPage();
    } catch (e) {
        output.innerHTML = `<div class="text-danger small"><i class="bi bi-x-circle me-1"></i>${escapeHtml(e.message)}</div>`;
    } finally {
        btn.disabled = false;
    }
}

async function deleteProvider(name) {
    const confirmed = await showConfirm(
        `Delete provider "${name}"?`,
        { icon: 'trash', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Delete' }
    );
    if (!confirmed) return;
    try {
        await apiFetch(`${API}/providers/${name}`, { method: 'DELETE' });
        showToast(`Provider "${name}" deleted.`, 'success');
        loadProvidersPage();
    } catch (e) {
        showToast(`Delete failed: ${e.message}`, 'danger');
    }
}
