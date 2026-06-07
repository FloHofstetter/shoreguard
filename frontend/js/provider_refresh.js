/**
 * Shoreguard — Provider Credential Refresh
 *
 * Modal launched from the providers list (the per-row refresh icon). Shows the
 * gateway-reported refresh status for each of a provider's credentials and lets
 * an operator configure a refresh strategy, rotate a credential now, or remove a
 * refresh configuration. The gateway is authoritative — ShoreGuard never stores
 * refresh state or secret material; only key *names* are ever sent to the audit
 * log server-side.
 *
 * Backs the OpenShell RPCs ConfigureProviderRefresh / GetProviderRefreshStatus /
 * RotateProviderCredential / DeleteProviderRefresh (upstream PR #1349, v0.0.57).
 */

const _REFRESH_STRATEGIES = [
    'static',
    'external',
    'oauth2_refresh_token',
    'oauth2_client_credentials',
    'google_service_account_jwt',
];

let _prModal = null;
let _prProvider = null;

function _ensureRefreshModal() {
    if (_prModal) return _prModal;

    const el = document.createElement('div');
    el.className = 'modal fade';
    el.id = 'providerRefreshModal';
    el.tabIndex = -1;
    el.innerHTML = `
        <div class="modal-dialog modal-lg">
            <div class="modal-content sg-card-themed">
                <div class="modal-header">
                    <h5 class="modal-title">
                        <i class="bi bi-arrow-repeat me-2"></i>Credential Refresh —
                        <span class="font-monospace" id="pr-name"></span>
                    </h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div id="pr-status" class="mb-4"></div>
                    <hr>
                    <h6 class="text-muted mb-3"><i class="bi bi-sliders me-1"></i>Configure refresh</h6>
                    <form id="pr-form" class="row g-2">
                        <div class="col-md-6">
                            <label class="form-label small">Credential key</label>
                            <input class="form-control form-control-sm" id="pr-key" required
                                   placeholder="e.g. ANTHROPIC_API_KEY">
                        </div>
                        <div class="col-md-6">
                            <label class="form-label small">Strategy</label>
                            <select class="form-select form-select-sm" id="pr-strategy">
                                ${_REFRESH_STRATEGIES.map(s => `<option value="${s}">${s}</option>`).join('')}
                            </select>
                        </div>
                        <div class="col-12">
                            <label class="form-label small">
                                Material <span class="text-muted">(one <code>key=value</code> per line)</span>
                            </label>
                            <textarea class="form-control form-control-sm font-monospace" id="pr-material"
                                      rows="3" placeholder="token_url=https://...\nclient_id=..."></textarea>
                        </div>
                        <div class="col-12">
                            <label class="form-label small">
                                Secret material keys <span class="text-muted">(comma-separated; stored encrypted)</span>
                            </label>
                            <input class="form-control form-control-sm font-monospace" id="pr-secret-keys"
                                   placeholder="client_secret, refresh_token">
                        </div>
                        <div class="col-12 text-end">
                            <button type="submit" class="btn btn-success btn-sm">
                                <i class="bi bi-check2 me-1"></i>Save configuration
                            </button>
                        </div>
                    </form>
                </div>
            </div>
        </div>`;
    document.body.appendChild(el);

    el.querySelector('#pr-form').addEventListener('submit', (e) => {
        e.preventDefault();
        _submitRefreshConfig();
    });
    // Delegated rotate/delete actions on the status table.
    el.querySelector('#pr-status').addEventListener('click', (e) => {
        const btn = e.target.closest('[data-pr-action]');
        if (!btn) return;
        const key = btn.dataset.key;
        if (btn.dataset.prAction === 'rotate') rotateProviderCredential(_prProvider, key);
        if (btn.dataset.prAction === 'delete') deleteProviderRefresh(_prProvider, key);
    });

    _prModal = new bootstrap.Modal(el);
    return _prModal;
}

function _renderRefreshStatus(credentials) {
    const container = document.getElementById('pr-status');
    if (!credentials || credentials.length === 0) {
        container.innerHTML = `<p class="text-muted small mb-0">
            <i class="bi bi-info-circle me-1"></i>No refresh configured for this provider yet.</p>`;
        return;
    }
    container.innerHTML = `
        <div class="table-responsive">
            <table class="table table-sm align-middle mb-0">
                <thead>
                    <tr>
                        <th>Credential</th><th>Strategy</th><th>Status</th>
                        <th>Expires</th><th>Next refresh</th><th class="text-end">Actions</th>
                    </tr>
                </thead>
                <tbody>
                    ${credentials.map(_renderRefreshRow).join('')}
                </tbody>
            </table>
        </div>`;
}

function _renderRefreshRow(c) {
    const err = c.last_error
        ? `<i class="bi bi-exclamation-triangle-fill text-danger ms-1" title="${escapeHtml(c.last_error)}"></i>`
        : '';
    return `
        <tr>
            <td class="font-monospace small"><strong>${escapeHtml(c.credential_key)}</strong></td>
            <td><span class="badge text-bg-secondary">${escapeHtml(c.strategy)}</span></td>
            <td class="small">${escapeHtml(c.status || '—')}${err}</td>
            <td class="small">${c.expires_at_ms ? formatTimestamp(c.expires_at_ms) : '—'}</td>
            <td class="small">${c.next_refresh_at_ms ? formatTimestamp(c.next_refresh_at_ms) : '—'}</td>
            <td class="text-end">
                <button class="btn btn-sm text-muted" data-pr-action="rotate" data-key="${escapeHtml(c.credential_key)}" title="Rotate now">
                    <i class="bi bi-arrow-repeat"></i>
                </button>
                <button class="btn btn-sm text-muted" data-pr-action="delete" data-key="${escapeHtml(c.credential_key)}" title="Remove refresh">
                    <i class="bi bi-trash3"></i>
                </button>
            </td>
        </tr>`;
}

async function openProviderRefresh(name) {
    _prProvider = name;
    _ensureRefreshModal();
    document.getElementById('pr-name').textContent = name;
    document.getElementById('pr-status').innerHTML = renderSpinner('Loading refresh status…');
    _prModal.show();
    await _reloadRefreshStatus();
}

async function _reloadRefreshStatus() {
    try {
        const resp = await apiFetch(`${API}/providers/${_prProvider}/refresh`);
        _renderRefreshStatus((resp && resp.credentials) || []);
    } catch (e) {
        document.getElementById('pr-status').innerHTML = renderError(e.message);
    }
}

async function _submitRefreshConfig() {
    const credential_key = document.getElementById('pr-key').value.trim();
    if (!credential_key) return;
    const strategy = document.getElementById('pr-strategy').value;
    const material = _parseKeyValueLines(document.getElementById('pr-material').value);
    const secret_material_keys = document.getElementById('pr-secret-keys').value
        .split(',').map(s => s.trim()).filter(Boolean);
    try {
        await apiFetch(`${API}/providers/${_prProvider}/refresh`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ credential_key, strategy, material, secret_material_keys }),
        });
        showToast(`Refresh configured for "${credential_key}".`, 'success');
        document.getElementById('pr-key').value = '';
        document.getElementById('pr-material').value = '';
        document.getElementById('pr-secret-keys').value = '';
        await _reloadRefreshStatus();
    } catch (e) {
        showToast(`Configure failed: ${e.message}`, 'danger');
    }
}

async function rotateProviderCredential(name, credentialKey) {
    try {
        await apiFetch(`${API}/providers/${name}/refresh/rotate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ credential_key: credentialKey }),
        });
        showToast(`Credential "${credentialKey}" rotated.`, 'success');
        await _reloadRefreshStatus();
    } catch (e) {
        showToast(`Rotation failed: ${e.message}`, 'danger');
    }
}

async function deleteProviderRefresh(name, credentialKey) {
    const confirmed = await showConfirm(
        `Remove refresh configuration for "${credentialKey}"?`,
        { icon: 'trash3', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Remove' }
    );
    if (!confirmed) return;
    try {
        await apiFetch(`${API}/providers/${name}/refresh?credential_key=${encodeURIComponent(credentialKey)}`, {
            method: 'DELETE',
        });
        showToast(`Refresh removed for "${credentialKey}".`, 'success');
        await _reloadRefreshStatus();
    } catch (e) {
        showToast(`Delete failed: ${e.message}`, 'danger');
    }
}
