function auditPage() {
    return {
        entries: [],
        loading: true,
        error: null,
        filters: { actor: '', action: '', resource_type: '' },

        get hasFilters() {
            return this.filters.actor || this.filters.action || this.filters.resource_type;
        },

        async load() {
            this.loading = true;
            this.error = null;
            try {
                const params = new URLSearchParams();
                params.set('limit', '1000');
                if (this.filters.actor) params.set('actor', this.filters.actor);
                if (this.filters.action) params.set('action', this.filters.action);
                if (this.filters.resource_type) params.set('resource_type', this.filters.resource_type);
                this.entries = await apiFetch(`/api/audit?${params}`);
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        clearFilters() {
            this.filters = { actor: '', action: '', resource_type: '' };
            this.load();
        },

        formatTs(iso) {
            if (!iso) return '';
            return new Date(iso).toLocaleString();
        },

        exportAudit(format) {
            const params = new URLSearchParams();
            params.set('format', format);
            if (this.filters.actor) params.set('actor', this.filters.actor);
            if (this.filters.action) params.set('action', this.filters.action);
            if (this.filters.resource_type) params.set('resource_type', this.filters.resource_type);
            window.open(`/api/audit/export?${params}`, '_blank');
        },
    };
}

document.addEventListener('alpine:init', () => {
    Alpine.data('auditPage', auditPage);
});
