/**
 * Shoreguard — Table Sorting & Filtering Mixin
 * Spread into any Alpine.js component: { ...myComponent(), ...sortableTable('name') }
 */

function sortableTable(defaultKey = '', defaultDir = 'asc') {
    return {
        _sortKey: defaultKey,
        _sortDir: defaultDir,
        _filterText: '',

        sortBy(key) {
            if (this._sortKey === key) {
                this._sortDir = this._sortDir === 'asc' ? 'desc' : 'asc';
            } else {
                this._sortKey = key;
                this._sortDir = 'asc';
            }
        },

        sortClass(key) {
            if (this._sortKey !== key) return 'sortable';
            return `sortable ${this._sortDir}`;
        },

        sorted(items) {
            if (!this._sortKey) return items;
            const dir = this._sortDir === 'asc' ? 1 : -1;
            return [...items].sort((a, b) => {
                const va = a[this._sortKey] ?? '';
                const vb = b[this._sortKey] ?? '';
                if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir;
                return String(va).localeCompare(String(vb)) * dir;
            });
        },

        filtered(items, ...keys) {
            if (!this._filterText) return items;
            const q = this._filterText.toLowerCase();
            return items.filter(item =>
                keys.some(k => String(item[k] ?? '').toLowerCase().includes(q))
            );
        },
    };
}
