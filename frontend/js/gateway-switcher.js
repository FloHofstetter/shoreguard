/**
 * Shoreguard — Topbar Gateway Switcher (M8)
 *
 * Replaces the read-only #gateway-status badge with a dropdown that lets
 * the operator pick the active gateway from any page. Uses pure URL
 * navigation (the existing /gateways/<name> routes); no client-side
 * "selected gateway" state is persisted, the URL path is the source of
 * truth.
 */

function gatewaySwitcher() {
    return {
        open: false,
        gateways: [],
        loading: false,
        loadedOnce: false,
        currentGw: '',

        init() {
            this.currentGw = document.documentElement.dataset.gateway || '';
        },

        async toggle() {
            this.open = !this.open;
            if (this.open && !this.loadedOnce) {
                await this.refresh();
            }
        },

        async refresh() {
            this.loading = true;
            try {
                const resp = await apiFetch(`${API_GLOBAL}/gateway/list`);
                this.gateways = (resp && resp.items) || [];
                this.loadedOnce = true;
            } catch (_e) {
                this.gateways = [];
            } finally {
                this.loading = false;
            }
        },

        navigate(name) {
            this.open = false;
            window.location.href = `/gateways/${name}`;
        },

        // Bootstrap badge color for the status dot.
        statusDotClass(status) {
            if (status === 'connected') return 'text-success';
            if (status === 'unreachable' || status === 'offline') return 'text-danger';
            return 'text-muted';
        },

        // Render labels object as "k=v k2=v2" for the dropdown row hint.
        formatLabels(labels) {
            if (!labels) return '';
            return Object.entries(labels)
                .map(([k, v]) => `${k}=${v}`)
                .join(' ');
        },

        // True when the URL is on a gateway-scoped page; false on global pages.
        get hasCurrent() {
            return Boolean(this.currentGw);
        },
    };
}

// Alpine.data registration for strict-CSP build
document.addEventListener('alpine:init', () => {
    Alpine.data('gatewaySwitcher', gatewaySwitcher);
});
