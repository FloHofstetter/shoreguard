/**
 * Shoreguard — Logs Page (Alpine.js)
 * Terminal-style log viewer with level toggles and text filter.
 */

function logsPage(name) {
    return {
        sandboxName: name,
        loading: true,
        error: '',
        allLogs: [],
        filters: { info: true, warn: true, error: true, text: '' },

        get filteredLogs() {
            return this.allLogs.filter(log => {
                const level = (log.level || 'info').toLowerCase();
                if (this.filters[level] !== undefined && !this.filters[level]) return false;
                if (this.filters.text && !log.message?.toLowerCase().includes(this.filters.text.toLowerCase())) return false;
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
                this.$nextTick(() => this._scrollToBottom());
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        toggleLevel(level) {
            this.filters[level] = !this.filters[level];
        },

        logCss(log) {
            const level = (log.level || 'info').toLowerCase();
            return `log-line log-${level}`;
        },

        _scrollToBottom() {
            const el = this.$refs.logContainer;
            if (el) el.scrollTop = el.scrollHeight;
        },
    };
}
