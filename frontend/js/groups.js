/**
 * Shoreguard — Group Management (Alpine.js component)
 */

const _GROUP_ROLE_BADGES = {
    admin: 'text-bg-danger',
    operator: 'text-bg-warning',
    viewer: 'text-bg-secondary',
};

function groupsPage() {
    return {
        groups: [],
        loading: true,
        error: '',

        // Create form
        showCreate: false,
        newName: '',
        newRole: 'viewer',
        newDesc: '',
        createError: '',

        // Edit form
        editGroup: null,
        editName: '',
        editRole: '',
        editDesc: '',
        editError: '',

        // Members modal
        membersGroup: null,
        members: [],
        allUsers: [],
        membersLoading: false,

        roleBadge(role) {
            return _GROUP_ROLE_BADGES[role] || 'text-bg-secondary';
        },

        formatDate(isoString) {
            return isoString ? new Date(isoString).toLocaleDateString() : '\u2014';
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                this.groups = await apiFetch('/api/auth/groups');
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        async createGroup() {
            if (!this.newName.trim()) {
                this.createError = 'Name is required.';
                return;
            }
            this.createError = '';
            try {
                await apiFetch('/api/auth/groups', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: this.newName.trim(),
                        role: this.newRole,
                        description: this.newDesc.trim() || null,
                    }),
                });
                showToast(`Group "${this.newName.trim()}" created.`, 'success');
                this.newName = '';
                this.newRole = 'viewer';
                this.newDesc = '';
                this.showCreate = false;
                this.load();
            } catch (e) {
                this.createError = e.message;
            }
        },

        startEdit(g) {
            this.editGroup = g;
            this.editName = g.name;
            this.editRole = g.role;
            this.editDesc = g.description || '';
            this.editError = '';
            const el = document.getElementById('editGroupModal');
            new bootstrap.Modal(el).show();
        },

        async saveEdit() {
            if (!this.editName.trim()) {
                this.editError = 'Name is required.';
                return;
            }
            this.editError = '';
            try {
                await apiFetch(`/api/auth/groups/${this.editGroup.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: this.editName.trim(),
                        role: this.editRole,
                        description: this.editDesc.trim() || null,
                    }),
                });
                showToast(`Group updated.`, 'success');
                bootstrap.Modal.getInstance(document.getElementById('editGroupModal'))?.hide();
                this.editGroup = null;
                this.load();
            } catch (e) {
                this.editError = e.message;
            }
        },

        async deleteGroup(g) {
            const confirmed = await showConfirm(
                `Delete group "${g.name}"? All memberships and gateway roles will be removed.`,
                { icon: 'trash', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Delete' }
            );
            if (!confirmed) return;
            try {
                await apiFetch(`/api/auth/groups/${g.id}`, { method: 'DELETE' });
                showToast(`Group "${g.name}" deleted.`, 'success');
                this.load();
            } catch (e) {
                showToast(`Delete failed: ${e.message}`, 'danger');
            }
        },

        async showMembers(g) {
            this.membersGroup = g;
            this.members = [];
            this.membersLoading = true;
            const el = document.getElementById('membersModal');
            new bootstrap.Modal(el).show();
            await this.loadMembers();
        },

        async loadMembers() {
            this.membersLoading = true;
            try {
                const [groupData, users] = await Promise.all([
                    apiFetch(`/api/auth/groups/${this.membersGroup.id}`),
                    apiFetch('/api/auth/users'),
                ]);
                this.members = groupData.members;
                // Available users = all users not yet in this group
                const memberIds = new Set(this.members.map(m => m.id));
                this.allUsers = users.filter(u => !memberIds.has(u.id));
            } catch (e) {
                showToast(`Failed to load members: ${e.message}`, 'danger');
            } finally {
                this.membersLoading = false;
            }
        },

        async addMember() {
            const select = document.getElementById('add-member-select');
            const userId = parseInt(select.value, 10);
            if (!userId) return;
            try {
                await apiFetch(`/api/auth/groups/${this.membersGroup.id}/members`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ user_id: userId }),
                });
                showToast('Member added.', 'success');
                await this.loadMembers();
                this.load();
            } catch (e) {
                showToast(`Failed: ${e.message}`, 'danger');
            }
        },

        async removeMember(userId) {
            try {
                await apiFetch(
                    `/api/auth/groups/${this.membersGroup.id}/members/${userId}`,
                    { method: 'DELETE' }
                );
                showToast('Member removed.', 'success');
                await this.loadMembers();
                this.load();
            } catch (e) {
                showToast(`Failed: ${e.message}`, 'danger');
            }
        },

        async showGatewayRoles(g) {
            await openGatewayRolesModal('group', g.id, g.name);
            this.load();
        },
    };
}
