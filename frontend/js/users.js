/**
 * Shoreguard — User & Service Principal Management (Alpine.js components)
 */

const _ROLE_BADGES = {
    admin: 'text-bg-danger',
    operator: 'text-bg-warning',
    viewer: 'text-bg-secondary',
};

// ─── Users List Page ────────────────────────────────────────────────────────

function usersPage() {
    return {
        users: [],
        sps: [],
        loading: true,
        error: '',

        roleBadge(role) {
            return _ROLE_BADGES[role] || 'text-bg-secondary';
        },

        formatDate(isoString) {
            return isoString ? new Date(isoString).toLocaleDateString() : '\u2014';
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                const [users, sps] = await Promise.all([
                    apiFetch('/api/auth/users'),
                    apiFetch('/api/auth/service-principals'),
                ]);
                this.users = users;
                this.sps = sps;
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        async deleteUser(u) {
            const confirmed = await showConfirm(
                `Delete user "${u.email}"?`,
                { icon: 'trash', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Delete' }
            );
            if (!confirmed) return;
            try {
                await apiFetch(`/api/auth/users/${u.id}`, { method: 'DELETE' });
                showToast(`User "${u.email}" deleted.`, 'success');
                this.load();
            } catch (e) {
                showToast(`Delete failed: ${e.message}`, 'danger');
            }
        },

        async showGatewayRoles(u) {
            await openGatewayRolesModal('user', u.id, u.email);
            this.load();
        },

        async deleteSP(sp) {
            const confirmed = await showConfirm(
                `Delete service principal "${sp.name}"? Existing tokens will stop working.`,
                { icon: 'trash', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Delete' }
            );
            if (!confirmed) return;
            try {
                await apiFetch(`/api/auth/service-principals/${sp.id}`, { method: 'DELETE' });
                showToast(`Service principal "${sp.name}" deleted.`, 'success');
                this.load();
            } catch (e) {
                showToast(`Delete failed: ${e.message}`, 'danger');
            }
        },

        async rotateSP(sp) {
            const confirmed = await showConfirm(
                `Rotate API key for "${sp.name}"? The current key will stop working immediately.`,
                { icon: 'arrow-repeat', iconColor: 'text-warning', btnClass: 'btn-warning', btnLabel: 'Rotate' }
            );
            if (!confirmed) return;
            try {
                const data = await apiFetch(`/api/auth/service-principals/${sp.id}/rotate`, { method: 'POST' });
                showKeyModal(data.key, sp.name);
                this.load();
            } catch (e) {
                showToast(`Rotate failed: ${e.message}`, 'danger');
            }
        },

        expiryBadge(sp) {
            if (!sp.expires_at) return '';
            const exp = new Date(sp.expires_at);
            const now = new Date();
            const daysLeft = Math.ceil((exp - now) / (1000 * 60 * 60 * 24));
            if (daysLeft <= 0) return '<span class="badge text-bg-danger">Expired</span>';
            if (daysLeft <= 30) return `<span class="badge text-bg-warning">${daysLeft}d left</span>`;
            return `<span class="badge text-bg-success">${daysLeft}d left</span>`;
        },

        async showSPGatewayRoles(sp) {
            await openGatewayRolesModal('sp', sp.id, sp.name);
            this.load();
        },
    };
}

function showKeyModal(key, name) {
    const existing = document.getElementById('showKeyModal');
    if (existing) existing.remove();

    document.body.insertAdjacentHTML('beforeend', `
        <div class="modal fade" id="showKeyModal" tabindex="-1">
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content sg-modal-themed">
                    <div class="modal-header border-bottom">
                        <h5 class="modal-title"><i class="bi bi-key me-2"></i>API Key — ${escapeHtml(name)}</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <div class="alert alert-warning mb-3">
                            <i class="bi bi-exclamation-triangle me-1"></i>
                            This key is shown only once. Copy it now.
                        </div>
                        <div class="input-group">
                            <input type="text" class="form-control font-monospace" value="${escapeHtml(key)}" readonly id="keyValue">
                            <button class="btn btn-outline-secondary" id="copyKeyBtn" title="Copy">
                                <i class="bi bi-clipboard"></i>
                            </button>
                        </div>
                    </div>
                    <div class="modal-footer border-0">
                        <button class="btn btn-outline-secondary" data-bs-dismiss="modal">Close</button>
                    </div>
                </div>
            </div>
        </div>
    `);

    const modalEl = document.getElementById('showKeyModal');
    const modal = new bootstrap.Modal(modalEl);
    modal.show();

    document.getElementById('copyKeyBtn').addEventListener('click', () => {
        navigator.clipboard.writeText(key).then(() => showToast('Key copied to clipboard', 'success'));
    });

    modalEl.addEventListener('hidden.bs.modal', () => modalEl.remove());
}


// ─── Gateway Roles Modal ───────────────────────────────────────────────────

async function openGatewayRolesModal(entityType, entityId, entityLabel) {
    const isUser = entityType === 'user';
    const basePath = isUser
        ? `/api/auth/users/${entityId}/gateway-roles`
        : `/api/auth/service-principals/${entityId}/gateway-roles`;

    const existing = document.getElementById('gatewayRolesModal');
    if (existing) existing.remove();

    document.body.insertAdjacentHTML('beforeend', `
        <div class="modal fade" id="gatewayRolesModal" tabindex="-1">
            <div class="modal-dialog modal-dialog-centered modal-lg">
                <div class="modal-content sg-modal-themed">
                    <div class="modal-header border-bottom">
                        <h5 class="modal-title">
                            <i class="bi bi-shield-lock me-2"></i>Gateway Roles: ${escapeHtml(entityLabel)}
                        </h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body" id="gw-roles-body">
                        ${renderSpinner('Loading gateway roles...')}
                    </div>
                    <div class="modal-footer border-0">
                        <button class="btn btn-outline-secondary" data-bs-dismiss="modal">Close</button>
                    </div>
                </div>
            </div>
        </div>
    `);

    const modalEl = document.getElementById('gatewayRolesModal');
    const modal = new bootstrap.Modal(modalEl);
    modal.show();

    async function renderRoles() {
        const body = document.getElementById('gw-roles-body');
        try {
            const [roles, gateways] = await Promise.all([
                apiFetch(basePath),
                apiFetch('/api/gateway/list'),
            ]);
            const gwNames = gateways.map(g => g.name);

            let html = '';
            if (roles.length > 0) {
                html += `<div class="table-responsive mb-3">
                    <table class="table table-striped table-sm align-middle">
                        <thead><tr><th>Gateway</th><th>Role</th><th class="text-end" style="width:60px"></th></tr></thead>
                        <tbody>
                            ${roles.map(r => `<tr>
                                <td><strong>${escapeHtml(r.gateway_name)}</strong></td>
                                <td><span class="badge ${_ROLE_BADGES[r.role] || 'text-bg-secondary'}">${escapeHtml(r.role)}</span></td>
                                <td class="text-end">
                                    <button class="btn btn-sm text-muted delete-btn" data-gw="${escapeHtml(r.gateway_name)}" title="Remove override">
                                        <i class="bi bi-trash3"></i>
                                    </button>
                                </td>
                            </tr>`).join('')}
                        </tbody>
                    </table>
                </div>`;
            } else {
                html += '<p class="text-muted mb-3">No gateway-specific role overrides. The global role applies everywhere.</p>';
            }

            // Add override form
            const availableGws = gwNames.filter(n => !roles.some(r => r.gateway_name === n));
            if (availableGws.length > 0) {
                html += `<div class="card bg-dark border-secondary">
                    <div class="card-body py-2">
                        <div class="row g-2 align-items-end">
                            <div class="col">
                                <label class="form-label small text-muted mb-1">Gateway</label>
                                <select id="gw-role-gw" class="form-select form-select-sm bg-dark text-light border-secondary">
                                    ${availableGws.map(n => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join('')}
                                </select>
                            </div>
                            <div class="col">
                                <label class="form-label small text-muted mb-1">Role</label>
                                <select id="gw-role-role" class="form-select form-select-sm bg-dark text-light border-secondary">
                                    <option value="admin">admin</option>
                                    <option value="operator">operator</option>
                                    <option value="viewer" selected>viewer</option>
                                </select>
                            </div>
                            <div class="col-auto">
                                <button id="gw-role-add" class="btn btn-sm btn-outline-success">
                                    <i class="bi bi-plus-lg me-1"></i>Add
                                </button>
                            </div>
                        </div>
                    </div>
                </div>`;
            }

            body.innerHTML = html;

            // Bind add button
            const addBtn = document.getElementById('gw-role-add');
            if (addBtn) {
                addBtn.addEventListener('click', async () => {
                    const gw = document.getElementById('gw-role-gw').value;
                    const role = document.getElementById('gw-role-role').value;
                    try {
                        await apiFetch(`${basePath}/${gw}`, {
                            method: 'PUT',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ role }),
                        });
                        showToast(`Gateway role set: ${role} on ${gw}`, 'success');
                        renderRoles();
                    } catch (e) {
                        showToast(`Failed: ${e.message}`, 'danger');
                    }
                });
            }

            // Bind delete buttons
            body.querySelectorAll('.delete-btn[data-gw]').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const gw = btn.dataset.gw;
                    try {
                        await apiFetch(`${basePath}/${gw}`, { method: 'DELETE' });
                        showToast(`Gateway role removed for ${gw}`, 'success');
                        renderRoles();
                    } catch (e) {
                        showToast(`Failed: ${e.message}`, 'danger');
                    }
                });
            });
        } catch (e) {
            body.innerHTML = renderError(e.message);
        }
    }

    await renderRoles();

    // Return a promise that resolves when modal is hidden
    return new Promise(resolve => {
        modalEl.addEventListener('hidden.bs.modal', () => {
            modalEl.remove();
            resolve();
        });
    });
}

// ─── Invite User Form ───────────────────────────────────────────────────────

function userNewForm() {
    return {
        email: '',
        role: 'viewer',
        error: '',
        loading: false,
        inviteUrl: '',

        async submit() {
            if (!this.email.trim()) {
                this.error = 'Email is required.';
                return;
            }
            this.error = '';
            this.loading = true;
            try {
                const data = await apiFetch('/api/auth/users', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email: this.email.trim(), role: this.role }),
                });
                this.inviteUrl = `${window.location.origin}/invite?token=${data.invite_token}`;
                showToast(`Invite for "${this.email.trim()}" created.`, 'success');
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },
    };
}

// ─── Service Principal Form ─────────────────────────────────────────────────

function spNewForm() {
    return {
        name: '',
        role: 'viewer',
        expiresDate: '',
        error: '',
        loading: false,
        spKey: '',

        async submit() {
            if (!this.name.trim()) {
                this.error = 'Name is required.';
                return;
            }
            this.error = '';
            this.loading = true;
            try {
                const payload = { name: this.name.trim(), role: this.role };
                if (this.expiresDate) {
                    payload.expires_at = new Date(this.expiresDate + 'T23:59:59Z').toISOString();
                }
                const data = await apiFetch('/api/auth/service-principals', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                this.spKey = data.key;
                showToast(`Service principal "${this.name.trim()}" created.`, 'success');
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },
    };
}
