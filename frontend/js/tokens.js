/**
 * Shoreguard — Gateway token (diagnostic)
 *
 * Issues/refreshes a gateway-minted JWT bound to ShoreGuard's identity, backing
 * the OpenShell IssueSandboxToken / RefreshSandboxToken RPCs (upstream PR #1404,
 * v0.0.57). This is a diagnostic to confirm token issuance against a gateway —
 * the token is bound to the caller (ShoreGuard), not to a sandbox. Admin only.
 */

function gatewayToken() {
    return {
        token: '',
        expiresAt: 0,
        busy: false,

        get expiryLabel() {
            if (!this.expiresAt) return 'non-expiring';
            return `expires ${formatTimestamp(this.expiresAt)}`;
        },

        async issue() {
            await this._call('issue');
        },

        async refresh() {
            await this._call('refresh');
        },

        async _call(kind) {
            this.busy = true;
            try {
                const resp = await apiFetch(`${API}/tokens/${kind}`, { method: 'POST' });
                this.token = (resp && resp.token) || '';
                this.expiresAt = (resp && resp.expires_at_ms) || 0;
                showToast(`Gateway token ${kind === 'issue' ? 'issued' : 'refreshed'}.`, 'success');
            } catch (e) {
                showToast(`Token ${kind} failed: ${e.message}`, 'danger');
            } finally {
                this.busy = false;
            }
        },
    };
}

document.addEventListener('alpine:init', () => {
    Alpine.data('gatewayToken', gatewayToken);
});
