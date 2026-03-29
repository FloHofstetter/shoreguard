/**
 * Shoreguard — User & Service Principal Management (admin-only)
 */

const _ROLE_BADGES = {
    admin: 'text-bg-danger',
    operator: 'text-bg-warning',
    viewer: 'text-bg-secondary',
};

async function loadUsersPage() {
    const container = document.getElementById('users-page-content');
    container.innerHTML = renderSpinner('Loading...');

    try {
        const [users, sps] = await Promise.all([
            apiFetch('/api/auth/users'),
            apiFetch('/api/auth/service-principals'),
        ]);

        container.innerHTML = `
            <!-- Users Section -->
            <div class="d-flex justify-content-between align-items-center mb-3">
                <h5 class="mb-0"><i class="bi bi-people me-2"></i>Users</h5>
                <div class="btn-group btn-group-sm">
                    <button class="btn btn-outline-secondary" onclick="loadUsersPage()" title="Refresh">
                        <i class="bi bi-arrow-clockwise"></i>
                    </button>
                    <button class="btn btn-outline-success" data-bs-toggle="modal" data-bs-target="#createUserModal">
                        <i class="bi bi-plus-lg me-1"></i>New User
                    </button>
                </div>
            </div>

            ${users.length > 0 ? `
                <div class="table-responsive mb-5">
                    <table class="table table-dark table-striped table-sm align-middle">
                        <thead>
                            <tr>
                                <th>Email</th>
                                <th>Role</th>
                                <th>Status</th>
                                <th class="d-none d-md-table-cell">Created</th>
                                <th class="text-end" style="width:60px">Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${users.map(u => {
                                const roleCls = _ROLE_BADGES[u.role] || 'text-bg-secondary';
                                const created = u.created_at ? new Date(u.created_at).toLocaleDateString() : '—';
                                const status = u.pending_invite
                                    ? '<span class="badge text-bg-info">Invited</span>'
                                    : '<span class="badge text-bg-success">Active</span>';
                                return `
                                    <tr>
                                        <td><strong>${escapeHtml(u.email)}</strong></td>
                                        <td><span class="badge ${roleCls}">${escapeHtml(u.role)}</span></td>
                                        <td>${status}</td>
                                        <td class="d-none d-md-table-cell small text-muted">${escapeHtml(created)}</td>
                                        <td class="text-end">
                                            <button class="btn btn-sm text-muted delete-btn" onclick="deleteUserById(${u.id}, '${escapeHtml(u.email)}')" title="Delete">
                                                <i class="bi bi-trash3"></i>
                                            </button>
                                        </td>
                                    </tr>`;
                            }).join('')}
                        </tbody>
                    </table>
                </div>
            ` : renderEmptyState('people', 'No users yet.',
                `<button class="btn btn-success btn-sm" data-bs-toggle="modal" data-bs-target="#createUserModal">
                    <i class="bi bi-plus me-1"></i>Create User</button>`)}

            <!-- Service Principals Section -->
            <div class="d-flex justify-content-between align-items-center mb-3">
                <h5 class="mb-0"><i class="bi bi-key me-2"></i>Service Principals</h5>
                <button class="btn btn-outline-success btn-sm" data-bs-toggle="modal" data-bs-target="#createSPModal">
                    <i class="bi bi-plus-lg me-1"></i>New
                </button>
            </div>

            ${sps.length > 0 ? `
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
                            ${sps.map(sp => {
                                const roleCls = _ROLE_BADGES[sp.role] || 'text-bg-secondary';
                                const created = sp.created_at ? new Date(sp.created_at).toLocaleDateString() : '—';
                                const lastUsed = sp.last_used ? formatTimeAgo(sp.last_used) : 'Never';
                                return `
                                    <tr>
                                        <td><strong>${escapeHtml(sp.name)}</strong></td>
                                        <td><span class="badge ${roleCls}">${escapeHtml(sp.role)}</span></td>
                                        <td class="d-none d-md-table-cell small text-muted">${escapeHtml(created)}</td>
                                        <td class="d-none d-md-table-cell small text-muted">${escapeHtml(lastUsed)}</td>
                                        <td class="text-end">
                                            <button class="btn btn-sm text-muted delete-btn" onclick="deleteSPById(${sp.id}, '${escapeHtml(sp.name)}')" title="Delete">
                                                <i class="bi bi-trash3"></i>
                                            </button>
                                        </td>
                                    </tr>`;
                            }).join('')}
                        </tbody>
                    </table>
                </div>
            ` : renderEmptyState('key', 'No service principals yet.',
                `<button class="btn btn-success btn-sm" data-bs-toggle="modal" data-bs-target="#createSPModal">
                    <i class="bi bi-plus me-1"></i>Create Service Principal</button>`)}
        `;
    } catch (e) {
        container.innerHTML = renderError(e.message);
    }
}

// ─── User Actions ──────────────────────────────────────────────────────────

async function createNewUser(e) {
    if (e) e.preventDefault();

    const email = document.getElementById('new-user-email').value.trim();
    const role = document.getElementById('new-user-role').value;
    const output = document.getElementById('create-user-output');
    const btn = document.getElementById('create-user-btn');

    if (!email) { output.innerHTML = '<div class="text-danger small">Email is required.</div>'; return; }

    btn.disabled = true;
    output.innerHTML = '<div class="text-muted small"><div class="spinner-border spinner-border-sm me-2"></div>Creating...</div>';

    try {
        const data = await apiFetch('/api/auth/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, role }),
        });

        const inviteUrl = `${window.location.origin}/invite?token=${data.invite_token}`;
        output.innerHTML = `
            <div class="alert alert-success small py-2 mb-0">
                <p class="mb-1"><i class="bi bi-check-circle me-1"></i>Invite created! Share this link with the user:</p>
                <div class="input-group input-group-sm">
                    <input type="text" class="form-control font-monospace" value="${escapeHtml(inviteUrl)}" readonly id="invite-url-value">
                    <button class="btn btn-outline-secondary" onclick="navigator.clipboard.writeText(document.getElementById('invite-url-value').value); showToast('Copied!', 'success')">
                        <i class="bi bi-clipboard"></i>
                    </button>
                </div>
            </div>`;
        showToast(`Invite sent to "${email}".`, 'success');
        loadUsersPage();
    } catch (e) {
        output.innerHTML = `<div class="text-danger small"><i class="bi bi-x-circle me-1"></i>${escapeHtml(e.message)}</div>`;
    } finally {
        btn.disabled = false;
    }
}

async function deleteUserById(id, email) {
    const confirmed = await showConfirm(
        `Delete user "${email}"?`,
        { icon: 'trash', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Delete' }
    );
    if (!confirmed) return;
    try {
        await apiFetch(`/api/auth/users/${id}`, { method: 'DELETE' });
        showToast(`User "${email}" deleted.`, 'success');
        loadUsersPage();
    } catch (e) {
        showToast(`Delete failed: ${e.message}`, 'danger');
    }
}

// ─── Service Principal Actions ─────────────────────────────────────────────

async function createNewSP(e) {
    if (e) e.preventDefault();

    const name = document.getElementById('new-sp-name').value.trim();
    const role = document.getElementById('new-sp-role').value;
    const output = document.getElementById('create-sp-output');
    const btn = document.getElementById('create-sp-btn');

    if (!name) { output.innerHTML = '<div class="text-danger small">Name is required.</div>'; return; }

    btn.disabled = true;
    output.innerHTML = '<div class="text-muted small"><div class="spinner-border spinner-border-sm me-2"></div>Creating...</div>';

    try {
        const data = await apiFetch('/api/auth/service-principals', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, role }),
        });

        output.innerHTML = `
            <div class="alert alert-success small py-2 mb-0">
                <p class="mb-1"><i class="bi bi-check-circle me-1"></i>Key created! Copy it now — it won't be shown again.</p>
                <div class="input-group input-group-sm">
                    <input type="text" class="form-control font-monospace" value="${escapeHtml(data.key)}" readonly id="new-sp-key-value">
                    <button class="btn btn-outline-secondary" onclick="navigator.clipboard.writeText(document.getElementById('new-sp-key-value').value); showToast('Copied!', 'success')">
                        <i class="bi bi-clipboard"></i>
                    </button>
                </div>
            </div>`;
        showToast(`Service principal "${name}" created.`, 'success');
        loadUsersPage();
    } catch (e) {
        output.innerHTML = `<div class="text-danger small"><i class="bi bi-x-circle me-1"></i>${escapeHtml(e.message)}</div>`;
    } finally {
        btn.disabled = false;
    }
}

async function deleteSPById(id, name) {
    const confirmed = await showConfirm(
        `Delete service principal "${name}"? Existing tokens will stop working.`,
        { icon: 'trash', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Delete' }
    );
    if (!confirmed) return;
    try {
        await apiFetch(`/api/auth/service-principals/${id}`, { method: 'DELETE' });
        showToast(`Service principal "${name}" deleted.`, 'success');
        loadUsersPage();
    } catch (e) {
        showToast(`Delete failed: ${e.message}`, 'danger');
    }
}
