/**
 * Policy Verification page — Alpine.js component.
 *
 * Lets users pick preset Z3 verification queries, supply parameters,
 * run them against the active sandbox policy, and view results with
 * counterexamples.
 */

/* global sgFetch */

function proverPage(gatewayName, sandboxName) {
    return {
        gatewayName,
        sandboxName,
        presets: [],
        selectedQueries: [],
        results: [],
        totalTimeMs: 0,
        running: false,
        error: '',

        async init() {
            await this.loadPresets();
        },

        async loadPresets() {
            try {
                const base = `/api/gateways/${encodeURIComponent(this.gatewayName)}/sandboxes/${encodeURIComponent(this.sandboxName)}`;
                const res = await sgFetch(`${base}/policy/verify/presets`);
                if (res.ok) {
                    this.presets = await res.json();
                }
            } catch (e) {
                console.error('Failed to load presets:', e);
            }
        },

        addQuery(preset) {
            const params = {};
            const paramDefs = preset.params || {};
            for (const pname of Object.keys(paramDefs)) {
                params[pname] = '';
            }
            this.selectedQueries.push({
                query_id: preset.query_id,
                label: preset.label,
                description: preset.description,
                paramDefs,
                params,
            });
        },

        removeQuery(idx) {
            this.selectedQueries.splice(idx, 1);
        },

        async runVerification() {
            this.running = true;
            this.error = '';
            this.results = [];
            try {
                const base = `/api/gateways/${encodeURIComponent(this.gatewayName)}/sandboxes/${encodeURIComponent(this.sandboxName)}`;
                const queries = this.selectedQueries.map(sq => ({
                    query_id: sq.query_id,
                    params: sq.params,
                }));
                const res = await sgFetch(`${base}/policy/verify`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ queries }),
                });
                if (res.ok) {
                    const data = await res.json();
                    this.results = data.results || [];
                    this.totalTimeMs = data.total_time_ms || 0;
                } else {
                    const err = await res.json().catch(() => ({}));
                    this.error = err.detail || `Verification failed (${res.status})`;
                }
            } catch (e) {
                this.error = 'Network error: ' + e.message;
            } finally {
                this.running = false;
            }
        },

        verdictIcon(verdict) {
            if (verdict === 'SAFE') return 'bi-check-circle-fill text-success';
            if (verdict === 'VULNERABLE') return 'bi-exclamation-triangle-fill text-danger';
            if (verdict === 'TIMEOUT') return 'bi-hourglass-split text-warning';
            return 'bi-x-circle text-secondary';
        },

        verdictColor(verdict) {
            if (verdict === 'SAFE') return 'text-success';
            if (verdict === 'VULNERABLE') return 'text-danger';
            if (verdict === 'TIMEOUT') return 'text-warning';
            return 'text-secondary';
        },

        verdictBorder(verdict) {
            if (verdict === 'SAFE') return 'border-success';
            if (verdict === 'VULNERABLE') return 'border-danger';
            if (verdict === 'TIMEOUT') return 'border-warning';
            return '';
        },
    };
}
