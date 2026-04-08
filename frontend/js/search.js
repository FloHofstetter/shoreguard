/**
 * Shoreguard — Command Palette (Ctrl+K / Cmd+K)
 * Fuzzy-ish search across navigation, gateways, users, and policies.
 */

function searchPalette() {
    return {
        open: false,
        query: '',
        results: [],
        selected: 0,
        _cache: null,

        toggle() {
            this.open = !this.open;
            if (this.open) {
                this.query = '';
                this.selected = 0;
                this._ensureCache();
                this.search();
                this.$nextTick(() => {
                    const input = document.getElementById('sg-palette-input');
                    if (input) input.focus();
                });
            }
        },

        close() {
            this.open = false;
        },

        async _ensureCache() {
            if (this._cache) return;
            this._cache = { nav: [], gateways: [], policies: [], users: [] };

            // Static navigation items
            this._cache.nav = [
                { name: 'Dashboard', url: '/', icon: 'bi-speedometer2' },
                { name: 'Gateways', url: '/gateways', icon: 'bi-hdd-network' },
                { name: 'Policy Presets', url: '/policies', icon: 'bi-shield-lock' },
                { name: 'Audit Log', url: '/audit', icon: 'bi-journal-text' },
                { name: 'Groups', url: '/groups', icon: 'bi-collection' },
                { name: 'Users', url: '/users', icon: 'bi-people' },
            ];

            // Fetch dynamic data
            try {
                const gwData = await apiFetch(`${API_GLOBAL}/gateway/list`);
                const gateways = (gwData && gwData.items) || [];
                this._cache.gateways = gateways.map(g => ({
                    name: g.name, url: `/gateways/${g.name}`, icon: 'bi-hdd-network',
                    hint: g.status,
                }));
            } catch { /* ignore */ }

            try {
                const presets = await apiFetch(`${API_GLOBAL}/policies/presets`);
                this._cache.policies = (presets || []).map(p => ({
                    name: p.name, url: `/policies/${p.name}`, icon: 'bi-shield-lock',
                    hint: p.description,
                }));
            } catch { /* ignore */ }

            try {
                const data = await apiFetch(`${API_GLOBAL}/auth/users`);
                this._cache.users = (data || []).map(u => ({
                    name: u.email, url: '/users', icon: 'bi-person',
                    hint: u.role,
                }));
            } catch { /* non-admin or unavailable */ }
        },

        search() {
            if (!this._cache) { this.results = []; return; }
            const q = this.query.toLowerCase().trim();
            const out = [];

            for (const [group, items] of Object.entries(this._cache)) {
                const label = { nav: 'Navigation', gateways: 'Gateways', policies: 'Policies', users: 'Users' }[group];
                const matches = q
                    ? items.filter(i => i.name.toLowerCase().includes(q))
                    : items;
                if (matches.length > 0) {
                    out.push({ type: 'group', label });
                    for (const m of matches) {
                        out.push({ type: 'item', ...m });
                    }
                }
            }

            this.results = out;
            this.selected = out.findIndex(r => r.type === 'item');
            if (this.selected === -1 && out.length > 0) this.selected = 0;
        },

        navigate(result) {
            if (result.type !== 'item') return;
            this.close();
            navigateTo(result.url);
        },

        onKeydown(e) {
            const items = this.results.filter(r => r.type === 'item');
            if (items.length === 0) return;

            if (e.key === 'ArrowDown') {
                e.preventDefault();
                let idx = this.selected;
                do { idx = (idx + 1) % this.results.length; } while (this.results[idx]?.type !== 'item');
                this.selected = idx;
                this._scrollToSelected();
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                let idx = this.selected;
                do { idx = (idx - 1 + this.results.length) % this.results.length; } while (this.results[idx]?.type !== 'item');
                this.selected = idx;
                this._scrollToSelected();
            } else if (e.key === 'Enter') {
                e.preventDefault();
                if (this.results[this.selected]) this.navigate(this.results[this.selected]);
            } else if (e.key === 'Escape') {
                e.preventDefault();
                this.close();
            }
        },

        _scrollToSelected() {
            this.$nextTick(() => {
                const el = document.querySelector('.sg-palette-item.active');
                if (el) el.scrollIntoView({ block: 'nearest' });
            });
        },
    };
}

// Global keyboard shortcut
document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        const el = document.querySelector('[x-data*="searchPalette"]');
        if (el && el._x_dataStack) {
            el._x_dataStack[0].toggle();
        }
    }
});
