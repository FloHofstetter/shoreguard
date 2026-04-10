function auditPage() {
    return {
        entries: [],
        loading: true,
        error: null,
        // Flat filter properties — the Alpine CSP expression parser has
        // trouble with nested `filters.actor` paths in x-model, so keep
        // each filter at the top level.
        filterActor: '',
        filterAction: '',
        filterResourceType: '',

        get hasFilters() {
            return this.filterActor || this.filterAction || this.filterResourceType;
        },

        async load() {
            this.loading = true;
            this.error = null;
            try {
                const params = new URLSearchParams();
                params.set('limit', '1000');
                if (this.filterActor) params.set('actor', this.filterActor);
                if (this.filterAction) params.set('action', this.filterAction);
                if (this.filterResourceType) params.set('resource_type', this.filterResourceType);
                const resp = await apiFetch(`/api/audit?${params}`);
                this.entries = Array.isArray(resp) ? resp : (resp.entries || resp.items || []);
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        clearFilters() {
            this.filterActor = '';
            this.filterAction = '';
            this.filterResourceType = '';
            this.load();
        },

        formatTs(iso) {
            if (!iso) return '';
            return new Date(iso).toLocaleString();
        },

        exportAudit(format) {
            const params = new URLSearchParams();
            params.set('format', format);
            if (this.filterActor) params.set('actor', this.filterActor);
            if (this.filterAction) params.set('action', this.filterAction);
            if (this.filterResourceType) params.set('resource_type', this.filterResourceType);
            window.open(`/api/audit/export?${params}`, '_blank');
        },
    };
}

document.addEventListener('alpine:init', () => {
    Alpine.data('auditPage', auditPage);
});
