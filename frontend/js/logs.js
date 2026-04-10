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
        showInfo: true,
        showWarn: true,
        showError: true,
        filterText: '',

        get filteredLogs() {
            return this.allLogs.filter(log => {
                const level = (log.level || 'info').toLowerCase();
                if (level === 'info' && !this.showInfo) return false;
                if (level === 'warn' && !this.showWarn) return false;
                if (level === 'error' && !this.showError) return false;
                if (this.filterText && !log.message?.toLowerCase().includes(this.filterText.toLowerCase())) return false;
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
            if (level === 'info') this.showInfo = !this.showInfo;
            else if (level === 'warn') this.showWarn = !this.showWarn;
            else if (level === 'error') this.showError = !this.showError;
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

document.addEventListener('alpine:init', () => {
    Alpine.data('logsPage', logsPage);
});
