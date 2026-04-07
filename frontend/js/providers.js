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
                    <a class="btn btn-success btn-sm" href="${window.location.pathname}/new">
                        <i class="bi bi-plus me-1"></i>Create Provider
                    </a>
                </div>`;
            return;
        }

        container.innerHTML = `
            <div class="table-responsive">
                <table class="table table-striped table-hover table-sm align-middle">
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
                ${_sgHasRole('operator') ? `
                <a class="btn btn-sm text-muted" href="${window.location.pathname}/${escapeHtml(provider.name)}/edit" title="Edit">
                    <i class="bi bi-pencil"></i>
                </a>
                <button class="btn btn-sm text-muted delete-btn" onclick="deleteProvider('${escapeHtml(provider.name)}')" title="Delete">
                    <i class="bi bi-trash3"></i>
                </button>` : ''}
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


// ─── Provider Form Page Component (create / edit) ──────────────────────────
// Used by /gateways/{gw}/providers/new and /gateways/{gw}/providers/{name}/edit

function providerForm(mode, providerName) {
    return {
        mode,
        providerName,
        form: { name: '', type: '', apiKey: '', creds: '', config: '' },
        credLabel: 'API_KEY',
        apiKeyPlaceholder: 'sk-...',
        submitting: false,
        output: '',

        async init() {
            await _ensureProviderTypes();
            if (this.mode === 'edit' && this.providerName) {
                try {
                    const providers = await apiFetch(`${API}/providers`);
                    const provider = providers.find(p => p.name === this.providerName);
                    if (provider) {
                        this.form.name = provider.name;
                        this.form.type = provider.type;
                        this.onTypeChange();
                        const configObj = provider.config || {};
                        this.form.config = Object.entries(configObj).map(([k, v]) => `${k}=${v}`).join('\n');
                    }
                } catch (e) {
                    this.output = `<div class="text-danger small">${escapeHtml(e.message)}</div>`;
                }
            }
        },

        onTypeChange() {
            this.credLabel = _getProviderCredKey(this.form.type);
            this.apiKeyPlaceholder = this.mode === 'edit' ? '(leave blank to keep current)' : 'sk-...';
        },

        async submit() {
            if (this.mode === 'create') {
                await this._create();
            } else {
                await this._update();
            }
        },

        async _create() {
            if (!this.form.name.trim()) { this.output = '<div class="text-danger small">Name is required.</div>'; return; }
            if (!this.form.type) { this.output = '<div class="text-danger small">Type is required.</div>'; return; }
            if (!this.form.apiKey.trim()) { this.output = '<div class="text-danger small">API key is required.</div>'; return; }

            this.submitting = true;
            this.output = '<div class="text-muted small"><div class="spinner-border spinner-border-sm me-2"></div>Creating...</div>';

            try {
                const extraCreds = _parseKeyValueLines(this.form.creds);
                const config = _parseKeyValueLines(this.form.config);
                await apiFetch(`${API}/providers`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: this.form.name.trim(),
                        type: this.form.type,
                        api_key: this.form.apiKey.trim(),
                        credentials: Object.keys(extraCreds).length > 0 ? extraCreds : undefined,
                        config: Object.keys(config).length > 0 ? config : undefined,
                    }),
                });
                showToast(`Provider "${this.form.name}" created.`, 'success');
                navigateTo(window.location.pathname.replace('/providers/new', '/providers'));
            } catch (e) {
                this.output = `<div class="text-danger small"><i class="bi bi-x-circle me-1"></i>${escapeHtml(e.message)}</div>`;
            } finally {
                this.submitting = false;
            }
        },

        async _update() {
            this.submitting = true;
            this.output = '<div class="text-muted small"><div class="spinner-border spinner-border-sm me-2"></div>Saving...</div>';

            try {
                const config = _parseKeyValueLines(this.form.config);
                const extraCreds = _parseKeyValueLines(this.form.creds);
                const body = {
                    type: this.form.type,
                    config: Object.keys(config).length > 0 ? config : {},
                };
                if (this.form.apiKey.trim() || Object.keys(extraCreds).length > 0) {
                    const keyName = _getProviderCredKey(this.form.type);
                    body.credentials = { ...extraCreds };
                    if (this.form.apiKey.trim()) body.credentials[keyName] = this.form.apiKey.trim();
                }
                await apiFetch(`${API}/providers/${this.providerName}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                showToast(`Provider "${this.providerName}" updated.`, 'success');
                navigateTo(window.location.pathname.replace(`/providers/${this.providerName}/edit`, '/providers'));
            } catch (e) {
                this.output = `<div class="text-danger small"><i class="bi bi-x-circle me-1"></i>${escapeHtml(e.message)}</div>`;
            } finally {
                this.submitting = false;
            }
        },
    };
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
