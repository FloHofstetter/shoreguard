/**
 * Bypass Detection page — Alpine.js component.
 *
 * Fetches bypass events from the ShoreGuard API and renders them as a
 * sortable, filterable table with summary cards.
 */

/* global Alpine, sgFetch */

const MITRE_LOOKUP = {
    iptables: 'T1562.004',
    nftables: 'T1562.004',
    nsenter: 'T1611',
    unshare: 'T1611',
    netns: 'T1611',
    ip_route: 'T1562.004',
    bypass: 'T1562.004',
};

const SEVERITY_LEVELS = ['FATAL', 'CRIT', 'HIGH', 'MED', 'LOW', 'INFO'];

const SEVERITY_BADGE = {
    FATAL: 'bg-dark text-white',
    CRIT: 'bg-danger',
    HIGH: 'bg-warning text-dark',
    MED: 'bg-info text-dark',
    LOW: 'bg-secondary',
    INFO: 'bg-light text-dark',
};

const SEVERITY_BTN = {
    FATAL: 'btn-dark',
    CRIT: 'btn-danger',
    HIGH: 'btn-warning',
    MED: 'btn-info',
    LOW: 'btn-secondary',
    INFO: 'btn-light',
};

function bypassPage(gatewayName, sandboxName) {
    return {
        gatewayName,
        sandboxName,
        loading: false,
        events: [],
        summary: { total: 0, by_technique: {}, by_severity: {}, latest_timestamp_ms: null },
        mitreLookup: MITRE_LOOKUP,
        severityLevels: SEVERITY_LEVELS,
        sevFilter: Object.fromEntries(SEVERITY_LEVELS.map(s => [s, true])),
        autoRefresh: false,
        _refreshTimer: null,

        async init() {
            await this.load();
            this.$watch('autoRefresh', (val) => {
                if (val) {
                    this._refreshTimer = setInterval(() => this.load(), 5000);
                } else if (this._refreshTimer) {
                    clearInterval(this._refreshTimer);
                    this._refreshTimer = null;
                }
            });
        },

        async load() {
            this.loading = true;
            try {
                const base = `/api/gateways/${encodeURIComponent(this.gatewayName)}/sandboxes/${encodeURIComponent(this.sandboxName)}`;
                const [evtRes, sumRes] = await Promise.all([
                    sgFetch(`${base}/bypass?limit=500`),
                    sgFetch(`${base}/bypass/summary`),
                ]);
                if (evtRes.ok) {
                    const data = await evtRes.json();
                    this.events = data.events || [];
                }
                if (sumRes.ok) {
                    this.summary = await sumRes.json();
                }
            } catch (e) {
                console.error('Failed to load bypass events:', e);
            } finally {
                this.loading = false;
            }
        },

        get filteredEvents() {
            return this.events.filter(evt => this.sevFilter[evt.event.severity] !== false);
        },

        severityBadge(sev) {
            return SEVERITY_BADGE[sev] || 'bg-secondary';
        },

        severityBtnClass(sev) {
            return SEVERITY_BTN[sev] || 'btn-secondary';
        },

        formatTime(ms) {
            if (!ms) return '—';
            const d = new Date(ms);
            return d.toLocaleTimeString(undefined, { hour12: false }) + '.' +
                   String(d.getMilliseconds()).padStart(3, '0');
        },
    };
}
