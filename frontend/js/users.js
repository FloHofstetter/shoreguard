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
    };
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
                const data = await apiFetch('/api/auth/service-principals', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: this.name.trim(), role: this.role }),
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
