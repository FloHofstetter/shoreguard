/**
 * Shoreguard — Webhooks Management Page
 *
 * Alpine.js component backing /webhooks. Lists every registered webhook,
 * supports inline create with HMAC-secret reveal, edit/delete via modal,
 * pause/resume via the is_active flag, send a test event, and view
 * delivery attempts in a per-webhook delivery log modal.
 */

function webhooksPage() {
    return {
        webhooks: [],
        loading: true,
        error: '',

        // Inline create form
        showCreate: false,
        newUrl: '',
        newEvents: '',
        newChannel: 'generic',
        newSmtpHost: '',
        newToAddrs: '',
        createError: '',

        // Last-created webhook secret reveal (cleared on dismiss)
        lastCreated: null,

        // Edit modal state
        editingId: null,
        editUrl: '',
        editEvents: '',
        editChannel: 'generic',
        editError: '',

        // Deliveries modal state
        deliveriesFor: null,
        deliveries: [],
        deliveriesLoading: false,

        formatDate(isoString) {
            return isoString ? new Date(isoString).toLocaleString() : '\u2014';
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                const resp = await apiFetch('/api/webhooks');
                this.webhooks = (resp && resp.items) || [];
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        _splitEvents(s) {
            return (s || '')
                .split(',')
                .map(x => x.trim())
                .filter(Boolean);
        },

        _splitToAddrs(s) {
            return (s || '')
                .split(',')
                .map(x => x.trim())
                .filter(Boolean);
        },

        async createWebhook() {
            this.createError = '';
            const url = (this.newUrl || '').trim();
            const events = this._splitEvents(this.newEvents);
            if (!url) { this.createError = 'URL is required.'; return; }
            if (events.length === 0) { this.createError = 'At least one event type is required.'; return; }

            const body = {
                url,
                event_types: events,
                channel_type: this.newChannel,
            };
            if (this.newChannel === 'email') {
                const host = (this.newSmtpHost || '').trim();
                const to = this._splitToAddrs(this.newToAddrs);
                if (!host || to.length === 0) {
                    this.createError = 'Email channel needs an SMTP host and at least one to-address.';
                    return;
                }
                body.extra_config = { smtp_host: host, to_addrs: to };
            }

            try {
                const created = await apiFetch('/api/webhooks', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                this.lastCreated = {
                    id: created.id,
                    url: created.url,
                    secret: created.secret,
                };
                showToast(`Webhook ${created.id} created.`, 'success');
                this.newUrl = '';
                this.newEvents = '';
                this.newChannel = 'generic';
                this.newSmtpHost = '';
                this.newToAddrs = '';
                this.showCreate = false;
                this.load();
            } catch (e) {
                this.createError = e.message;
            }
        },

        async toggleActive(wh) {
            try {
                await apiFetch(`/api/webhooks/${wh.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ is_active: !wh.is_active }),
                });
                showToast(`Webhook ${wh.id} ${wh.is_active ? 'paused' : 'resumed'}.`, 'success');
                this.load();
            } catch (e) {
                showToast(`Failed: ${e.message}`, 'danger');
            }
        },

        startEdit(wh) {
            this.editingId = wh.id;
            this.editUrl = wh.url;
            this.editChannel = wh.channel_type || 'generic';
            this.editEvents = (wh.event_types || []).join(', ');
            this.editError = '';
            const el = document.getElementById('editWebhookModal');
            new bootstrap.Modal(el).show();
        },

        async submitEdit() {
            this.editError = '';
            const events = this._splitEvents(this.editEvents);
            if (!this.editUrl.trim()) { this.editError = 'URL is required.'; return; }
            if (events.length === 0) { this.editError = 'At least one event type is required.'; return; }
            try {
                await apiFetch(`/api/webhooks/${this.editingId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: this.editUrl.trim(),
                        event_types: events,
                        channel_type: this.editChannel,
                    }),
                });
                showToast(`Webhook ${this.editingId} updated.`, 'success');
                bootstrap.Modal.getInstance(document.getElementById('editWebhookModal'))?.hide();
                this.load();
            } catch (e) {
                this.editError = e.message;
            }
        },

        async deleteWebhook(wh) {
            const confirmed = await showConfirm(
                `Delete webhook ${wh.id}? "${wh.url}" will stop receiving events.`,
                { icon: 'trash', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Delete' }
            );
            if (!confirmed) return;
            try {
                await apiFetch(`/api/webhooks/${wh.id}`, { method: 'DELETE' });
                showToast(`Webhook ${wh.id} deleted.`, 'success');
                this.load();
            } catch (e) {
                showToast(`Delete failed: ${e.message}`, 'danger');
            }
        },

        async testWebhook(wh) {
            try {
                await apiFetch(`/api/webhooks/${wh.id}/test`, { method: 'POST' });
                showToast(`Test event fired to webhook ${wh.id}.`, 'success');
            } catch (e) {
                showToast(`Test failed: ${e.message}`, 'danger');
            }
        },

        async showDeliveries(wh) {
            this.deliveriesFor = wh;
            this.deliveries = [];
            this.deliveriesLoading = true;
            const el = document.getElementById('deliveriesModal');
            new bootstrap.Modal(el).show();
            try {
                this.deliveries = await apiFetch(`/api/webhooks/${wh.id}/deliveries?limit=100`);
            } catch (e) {
                showToast(`Failed to load deliveries: ${e.message}`, 'danger');
            } finally {
                this.deliveriesLoading = false;
            }
        },
    };
}

document.addEventListener('alpine:init', () => {
    Alpine.data('webhooksPage', () => ({
        ...webhooksPage(),
        ...sortableTable('id', 'desc'),
    }));
});
