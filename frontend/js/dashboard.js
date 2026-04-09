function dashboardPage() {
    return {
        loading: true,
        error: '',
        gateways: [],
        sandboxCount: 0,
        approvalCount: 0,
        presetCount: 0,
        auditEntries: [],

        get connectedCount() {
            return this.gateways.filter(g => g.status === 'connected').length;
        },

        async init() {
            this.loading = true;
            try {
                const [gwData, presets] = await Promise.all([
                    apiFetch(`${API_GLOBAL}/gateway/list`).catch(() => null),
                    apiFetch(`${API_GLOBAL}/policies/presets`).catch(() => null),
                ]);
                this.gateways = (gwData && gwData.items) || [];
                this.presetCount = (presets || []).length;

                // Sandbox count (gateway-scoped)
                if (GW) {
                    try {
                        const sbs = await apiFetch(`${API}/sandboxes`);
                        this.sandboxCount = (sbs || []).length;
                    } catch { /* no gateway */ }
                }

                // Recent audit entries (admin only, fails silently for non-admin)
                try {
                    const audit = await apiFetch(`${API_GLOBAL}/audit?limit=10`);
                    this.auditEntries = (audit && audit.entries) || [];
                } catch { /* non-admin or audit unavailable */ }
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },
    };
}

document.addEventListener('alpine:init', () => {
    Alpine.data('dashboardPage', dashboardPage);
});
