/**
 * Shoreguard — Logs Page (Alpine.js)
 * Terminal-style log viewer with level toggles, OCSF filters and text filter.
 */

function logsPage(name) {
    return {
        sandboxName: name,
        loading: true,
        error: '',
        allLogs: [],
        showInfo: true,
        showWarn: true,
        showError: true,
        showOcsf: true,
        serverOcsfOnly: false,
        classFilter: {},
        expanded: {},
        filterText: '',

        get availableClasses() {
            const seen = new Set();
            for (const log of this.allLogs) {
                if (log.ocsf && log.ocsf.class_prefix) {
                    seen.add(log.ocsf.class_prefix);
                }
            }
            return Array.from(seen).sort();
        },

        get filteredLogs() {
            const needle = this.filterText.toLowerCase();
            return this.allLogs.filter(log => {
                if (log.ocsf) {
                    if (!this.showOcsf) return false;
                    const cls = log.ocsf.class_prefix;
                    if (cls && this.classFilter[cls] === false) return false;
                } else {
                    const level = (log.level || 'info').toLowerCase();
                    if (level === 'info' && !this.showInfo) return false;
                    if (level === 'warn' && !this.showWarn) return false;
                    if (level === 'error' && !this.showError) return false;
                }
                if (needle && !log.message?.toLowerCase().includes(needle)) return false;
                return true;
            });
        },

        async init() {
            // Cross-link from an approvals page: /logs?text=/usr/bin/curl
            const qs = new URLSearchParams(window.location.search);
            const text = qs.get('text');
            if (text) {
                this.filterText = text;
            }
            await this.load();
        },

        // Navigate to the sandbox approvals page, passing the denied
        // binary/host as a hash fragment so the approvals page can scroll
        // to the matching chunk.
        goToApprovals(log) {
            if (!log.ocsf) return;
            const binary = log.ocsf.binary || '';
            // Best-effort host: pull from bracket_fields[policy] is too
            // indirect; the summary usually contains "-> host:port" for
            // NET/HTTP events. Leave empty if we can't figure it out.
            let host = '';
            const m = (log.ocsf.summary || '').match(/->\s+([^\s:]+)/);
            if (m) host = m[1];
            const frag = [];
            if (binary) frag.push(`binary=${encodeURIComponent(binary)}`);
            if (host) frag.push(`host=${encodeURIComponent(host)}`);
            const hash = frag.length ? '#' + frag.join('&') : '';
            window.location.href = `/gateways/${GW}/sandboxes/${this.sandboxName}/approvals${hash}`;
        },

        isDeniedOcsf(log) {
            if (!log.ocsf) return false;
            const d = (log.ocsf.disposition || '').toUpperCase();
            return d === 'DENIED' || d === 'BLOCKED';
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                const params = new URLSearchParams({ lines: SG.config.logLinesDefault });
                if (this.serverOcsfOnly) params.set('ocsf_only', 'true');
                this.allLogs = await apiFetch(`${API}/sandboxes/${name}/logs?${params.toString()}`);
                // Default every newly-seen OCSF class to "on".
                for (const cls of this.availableClasses) {
                    if (this.classFilter[cls] === undefined) {
                        this.classFilter[cls] = true;
                    }
                }
                this.$nextTick(() => this._scrollToBottom());
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        toggleServerOcsfOnly() {
            this.serverOcsfOnly = !this.serverOcsfOnly;
            this.load();
        },

        toggleLevel(level) {
            if (level === 'info') this.showInfo = !this.showInfo;
            else if (level === 'warn') this.showWarn = !this.showWarn;
            else if (level === 'error') this.showError = !this.showError;
            else if (level === 'ocsf') this.showOcsf = !this.showOcsf;
        },

        toggleClass(cls) {
            this.classFilter[cls] = !this.classFilter[cls];
        },

        toggleExpand(idx) {
            this.expanded[idx] = !this.expanded[idx];
        },

        logCss(log) {
            if (log.ocsf) {
                const disp = (log.ocsf.disposition || '').toLowerCase();
                const cls = ['log-line', 'log-ocsf'];
                if (disp) cls.push(`log-ocsf-${disp}`);
                return cls.join(' ');
            }
            const level = (log.level || 'info').toLowerCase();
            return `log-line log-${level}`;
        },

        hasExpandable(log) {
            if (!log.ocsf) return false;
            const bf = log.ocsf.bracket_fields || {};
            const gf = log.ocsf.fields || {};
            return Object.keys(bf).length > 0 || Object.keys(gf).length > 0;
        },

        _scrollToBottom() {
            const el = this.$refs.logContainer;
            if (el) el.scrollTop = el.scrollHeight;
        },
    };
}

document.addEventListener('alpine:init', () => {
    Alpine.data('logsPage', logsPage);
});
