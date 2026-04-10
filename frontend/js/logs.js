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
            await this.load();
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                this.allLogs = await apiFetch(`${API}/sandboxes/${name}/logs?lines=${SG.config.logLinesDefault}`);
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
