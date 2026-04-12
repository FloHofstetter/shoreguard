/**
 * SBOM viewer page — Alpine.js component (M21).
 *
 * Loads + displays a CycloneDX SBOM snapshot for one sandbox: components
 * table (paginated, search + severity filter) and vulnerabilities list.
 * Admins can upload, replace, and delete the snapshot.
 */

/* global sgFetch */

const SEVERITY_BADGE = {
    CRITICAL: 'bg-danger',
    HIGH: 'bg-warning text-dark',
    MEDIUM: 'bg-info text-dark',
    LOW: 'bg-secondary',
    INFO: 'bg-light text-dark',
    UNKNOWN: 'bg-light text-dark',
    CLEAN: 'bg-success',
};

const SEVERITY_BTN = {
    CRITICAL: 'btn-danger',
    HIGH: 'btn-warning',
    MEDIUM: 'btn-info',
    LOW: 'btn-secondary',
    CLEAN: 'btn-success',
};

const SEVERITY_FILTERS = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'CLEAN'];

function sbomPage(gatewayName, sandboxName) {
    return {
        gatewayName,
        sandboxName,
        loading: false,
        vulnsLoading: false,
        snapshot: null,
        tab: 'components',
        components: [],
        vulns: [],
        search: '',
        severity: '',
        offset: 0,
        limit: 50,
        total: 0,
        severityFilters: SEVERITY_FILTERS,

        get baseUrl() {
            return `/api/gateways/${encodeURIComponent(this.gatewayName)}/sandboxes/${encodeURIComponent(this.sandboxName)}/sbom`;
        },

        async init() {
            await this.loadSnapshot();
        },

        async loadSnapshot() {
            this.loading = true;
            try {
                const resp = await sgFetch(this.baseUrl);
                if (resp.status === 404) {
                    this.snapshot = null;
                    return;
                }
                if (!resp.ok) {
                    console.error('Failed to load SBOM snapshot:', resp.status);
                    this.snapshot = null;
                    return;
                }
                this.snapshot = await resp.json();
                await this.loadComponents();
            } finally {
                this.loading = false;
            }
        },

        async loadComponents() {
            if (!this.snapshot) return;
            const params = new URLSearchParams({
                offset: String(this.offset),
                limit: String(this.limit),
            });
            if (this.search) params.set('search', this.search);
            if (this.severity) params.set('severity', this.severity);
            try {
                const resp = await sgFetch(`${this.baseUrl}/components?${params.toString()}`);
                if (resp.ok) {
                    const data = await resp.json();
                    this.components = data.items || [];
                    this.total = data.total || 0;
                }
            } catch (e) {
                console.error('Failed to load components:', e);
            }
        },

        async loadVulns() {
            if (!this.snapshot || this.vulns.length > 0) return;
            this.vulnsLoading = true;
            try {
                const resp = await sgFetch(`${this.baseUrl}/vulnerabilities`);
                if (resp.ok) {
                    const data = await resp.json();
                    this.vulns = data.vulnerabilities || [];
                }
            } catch (e) {
                console.error('Failed to load vulnerabilities:', e);
            } finally {
                this.vulnsLoading = false;
            }
        },

        async uploadFromInput(event) {
            const file = event.target.files && event.target.files[0];
            if (!file) return;
            const text = await file.text();
            event.target.value = '';
            await this.upload(text);
        },

        async upload(rawJson) {
            this.loading = true;
            try {
                const resp = await sgFetch(this.baseUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: rawJson,
                });
                if (!resp.ok) {
                    let msg = 'Upload failed';
                    try {
                        const err = await resp.json();
                        msg = err.detail || msg;
                    } catch (_) { /* ignore */ }
                    alert(`SBOM upload failed: ${msg}`);
                    return;
                }
                this.vulns = [];
                this.offset = 0;
                this.search = '';
                this.severity = '';
                await this.loadSnapshot();
            } finally {
                this.loading = false;
            }
        },

        async confirmDelete() {
            if (!confirm('Delete the SBOM snapshot for this sandbox?')) return;
            const resp = await sgFetch(this.baseUrl, { method: 'DELETE' });
            if (resp.ok || resp.status === 204) {
                this.snapshot = null;
                this.components = [];
                this.vulns = [];
                this.total = 0;
            } else {
                alert('Failed to delete SBOM');
            }
        },

        downloadRaw() {
            window.location.assign(`${this.baseUrl}/raw`);
        },

        severityBadge(sev) {
            if (!sev) return SEVERITY_BADGE.CLEAN;
            return SEVERITY_BADGE[sev] || 'bg-secondary';
        },

        severityBtnClass(sev) {
            return SEVERITY_BTN[sev] || 'btn-secondary';
        },

        formatTime(iso) {
            if (!iso) return '—';
            try {
                return new Date(iso).toLocaleString();
            } catch (_) {
                return iso;
            }
        },

        curlExample() {
            const url = `${window.location.origin}${this.baseUrl}`;
            return `curl -X POST "${url}" \\\n  -H "Authorization: Bearer $SHOREGUARD_TOKEN" \\\n  -H "Content-Type: application/json" \\\n  --data-binary @sbom.cdx.json`;
        },
    };
}

window.sbomPage = sbomPage;
