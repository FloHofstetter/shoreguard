/**
 * Shoreguard — API Key Management (admin-only)
 * List, create, delete API keys with role assignment.
 */

const _ROLE_BADGES = {
    admin: 'text-bg-danger',
    operator: 'text-bg-warning',
    viewer: 'text-bg-secondary',
};

async function loadKeysPage() {
    const container = document.getElementById('keys-page-content');
    container.innerHTML = renderSpinner('Loading API keys...');

    try {
        const keys = await apiFetch('/api/auth/keys');

        if (keys.length === 0) {
            container.innerHTML = renderEmptyState('key', 'No API keys created yet.',
                `<button class="btn btn-success btn-sm" data-bs-toggle="modal" data-bs-target="#createKeyModal">
                    <i class="bi bi-plus me-1"></i>Create Key
                </button>`
            );
            return;
        }

        container.innerHTML = `
            <div class="table-responsive">
                <table class="table table-dark table-striped table-sm align-middle">
                    <thead>
                        <tr>
                            <th>Name</th>
                            <th>Role</th>
                            <th class="d-none d-md-table-cell">Created</th>
                            <th class="d-none d-md-table-cell">Last Used</th>
                            <th class="text-end" style="width:60px">Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${keys.map(k => {
                            const roleCls = _ROLE_BADGES[k.role] || 'text-bg-secondary';
                            const created = k.created_at ? new Date(k.created_at).toLocaleDateString() : '—';
                            const lastUsed = k.last_used ? formatTimeAgo(k.last_used) : 'Never';
                            return `
                                <tr>
                                    <td><strong>${escapeHtml(k.name)}</strong></td>
                                    <td><span class="badge ${roleCls}">${escapeHtml(k.role)}</span></td>
                                    <td class="d-none d-md-table-cell small text-muted">${escapeHtml(created)}</td>
                                    <td class="d-none d-md-table-cell small text-muted">${escapeHtml(lastUsed)}</td>
                                    <td class="text-end">
                                        <button class="btn btn-sm text-muted delete-btn" onclick="deleteKey('${escapeHtml(k.name)}')" title="Delete">
                                            <i class="bi bi-trash3"></i>
                                        </button>
                                    </td>
                                </tr>`;
                        }).join('')}
                    </tbody>
                </table>
            </div>`;
    } catch (e) {
        container.innerHTML = renderError(e.message);
    }
}

async function createNewKey(e) {
    if (e) e.preventDefault();

    const name = document.getElementById('new-key-name').value.trim();
    const role = document.getElementById('new-key-role').value;
    const output = document.getElementById('create-key-output');
    const btn = document.getElementById('create-key-btn');

    if (!name) { output.innerHTML = '<div class="text-danger small">Name is required.</div>'; return; }

    btn.disabled = true;
    output.innerHTML = '<div class="text-muted small"><div class="spinner-border spinner-border-sm me-2"></div>Creating...</div>';

    try {
        const data = await apiFetch('/api/auth/keys', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, role }),
        });

        // Show the key once — it cannot be retrieved again
        output.innerHTML = `
            <div class="alert alert-success small py-2 mb-0">
                <p class="mb-1"><i class="bi bi-check-circle me-1"></i>Key created! Copy it now — it won't be shown again.</p>
                <div class="input-group input-group-sm">
                    <input type="text" class="form-control font-monospace" value="${escapeHtml(data.key)}" readonly id="new-key-value">
                    <button class="btn btn-outline-secondary" onclick="navigator.clipboard.writeText(document.getElementById('new-key-value').value); showToast('Copied!', 'success')">
                        <i class="bi bi-clipboard"></i>
                    </button>
                </div>
            </div>`;
        showToast(`Key "${name}" created.`, 'success');
        loadKeysPage();
    } catch (e) {
        output.innerHTML = `<div class="text-danger small"><i class="bi bi-x-circle me-1"></i>${escapeHtml(e.message)}</div>`;
    } finally {
        btn.disabled = false;
    }
}

async function deleteKey(name) {
    const confirmed = await showConfirm(
        `Delete API key "${name}"? This cannot be undone.`,
        { icon: 'trash', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Delete' }
    );
    if (!confirmed) return;
    try {
        await apiFetch(`/api/auth/keys/${name}`, { method: 'DELETE' });
        showToast(`Key "${name}" deleted.`, 'success');
        loadKeysPage();
    } catch (e) {
        showToast(`Delete failed: ${e.message}`, 'danger');
    }
}
