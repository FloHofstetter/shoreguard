/**
 * Shoreguard — Sandbox List, Detail & Terminal (Alpine.js)
 */

// ─── Sandbox List ────────────────────────────────────────────────────────────

function sandboxList() {
    return {
        loading: true,
        error: '',
        sandboxes: [],

        async load() {
            this.loading = true;
            this.error = '';
            try {
                this.sandboxes = await apiFetch(`${API}/sandboxes`);
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        async deleteSandbox(name) {
            const confirmed = await showConfirm(
                `Delete sandbox "${name}"? This cannot be undone.`,
                { icon: 'trash', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Delete' }
            );
            if (!confirmed) return;
            try {
                await apiFetch(`${API}/sandboxes/${name}`, { method: 'DELETE' });
                showToast(`Sandbox "${name}" deleted.`, 'success');
                this.load();
            } catch (e) {
                showToast(`Delete failed: ${e.message}`, 'danger');
            }
        },
    };
}


// ─── Sandbox Detail ─────────────────────────────────────────────────────────

function sandboxDetail(name) {
    return {
        sandboxName: name,
        loading: true,
        error: '',
        sandbox: null,
        pendingCount: 0,
        networkCount: 0,
        policy: null,
        metaForm: { description: '' },
        metaLabels: [],
        newMetaKey: '',
        newMetaVal: '',
        saving: false,
        saveOutput: '',

        async init() {
            await this.load();
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                const [sb, pendingApprovals, policyData] = await Promise.all([
                    apiFetch(`${API}/sandboxes/${name}`),
                    apiFetch(`${API}/sandboxes/${name}/approvals/pending`).catch(() => []),
                    apiFetch(`${API}/sandboxes/${name}/policy`).catch(() => null),
                ]);

                this.sandbox = sb;
                this.metaForm.description = sb.description || '';
                this.metaLabels = Object.entries(sb.labels || {}).map(([k, v]) => ({ key: k, val: v }));
                this.pendingCount = pendingApprovals?.length || 0;
                this.policy = policyData?.policy || null;
                this.networkCount = this.policy ? Object.keys(this.policy.network_policies || {}).length : 0;

                // Update subnav phase badge
                this._updateNavPhase(sb);

                // Connect WebSocket for live updates
                if (typeof connectWebSocket === 'function') {
                    connectWebSocket(sb.name, sb.id);
                }
            } catch (e) {
                this.error = `Sandbox "${name}" not found.`;
            } finally {
                this.loading = false;
            }
        },

        phaseBadge() {
            return SG.badges.phase[this.sandbox?.phase] || 'text-bg-secondary';
        },

        networkLabel() {
            const n = this.networkCount;
            return n === 1 ? '1 network rule' : n + ' network rules';
        },

        pendingLabel() {
            const n = this.pendingCount;
            if (n === 0) return 'No pending requests';
            return n === 1 ? '1 request needs review' : n + ' requests need review';
        },

        _updateNavPhase(sb) {
            const phaseBadge = document.getElementById('ctx-sandbox-phase');
            if (phaseBadge && sb) {
                phaseBadge.className = `badge ${SG.badges.phase[sb.phase] || 'text-bg-secondary'}`;
                phaseBadge.textContent = sb.phase;
            }
        },

        addMetaLabel() {
            const key = this.newMetaKey.trim();
            const val = this.newMetaVal.trim();
            if (!key) return;
            if (this.metaLabels.some(r => r.key === key)) return;
            if (this.metaLabels.length >= 20) return;
            this.metaLabels.push({ key, val });
            this.newMetaKey = '';
            this.newMetaVal = '';
        },

        removeMetaLabel(key) {
            this.metaLabels = this.metaLabels.filter(r => r.key !== key);
        },

        async saveMeta() {
            this.saving = true;
            this.saveOutput = '';
            const body = {};
            const desc = this.metaForm.description.trim();
            body.description = desc || null;
            if (this.metaLabels.length > 0) {
                const labels = {};
                for (const r of this.metaLabels) labels[r.key] = r.val;
                body.labels = labels;
            } else {
                body.labels = null;
            }
            try {
                await apiFetch(`${API}/sandboxes/${this.sandboxName}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                this.saveOutput = '<span class="text-success"><i class="bi bi-check-circle me-1"></i>Saved</span>';
                setTimeout(() => { this.saveOutput = ''; }, 2000);
                this.load();
            } catch (e) {
                this.saveOutput = `<span class="text-danger">${escapeHtml(e.message)}</span>`;
            }
            this.saving = false;
        },
    };
}


// ─── Terminal Page ───────────────────────────────────────────────────────────

function terminalPage(sandboxName) {
    return {
        sandboxName,
        commandInput: '',
        outputLines: [],
        history: [],
        historyIdx: -1,
        running: false,

        handleKey(event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                this.runCommand();
            } else if (event.key === 'ArrowUp') {
                event.preventDefault();
                if (this.historyIdx < this.history.length - 1) {
                    this.historyIdx++;
                    this.commandInput = this.history[this.history.length - 1 - this.historyIdx];
                }
            } else if (event.key === 'ArrowDown') {
                event.preventDefault();
                if (this.historyIdx > 0) {
                    this.historyIdx--;
                    this.commandInput = this.history[this.history.length - 1 - this.historyIdx];
                } else {
                    this.historyIdx = -1;
                    this.commandInput = '';
                }
            }
        },

        async runCommand() {
            const cmd = this.commandInput.trim();
            if (!cmd) return;

            this.history.push(cmd);
            this.historyIdx = -1;
            this.commandInput = '';
            this.running = true;

            this.outputLines.push({ text: `$ ${cmd}`, css: 'color:var(--sg-accent)' });
            this._scrollOutput();

            try {
                const result = await apiFetch(`${API}/sandboxes/${this.sandboxName}/exec`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ command: cmd }),
                });

                if (result.stdout) {
                    this.outputLines.push({ text: result.stdout, css: 'white-space:pre-wrap' });
                }
                if (result.stderr) {
                    this.outputLines.push({ text: result.stderr, css: 'white-space:pre-wrap', cls: 'log-error' });
                }
                if (result.exit_code !== 0) {
                    this.outputLines.push({ text: `exit code: ${result.exit_code}`, cls: 'log-error' });
                }
            } catch (e) {
                this.outputLines.push({ text: `Error: ${e.message}`, cls: 'log-error' });
            }

            this.running = false;
            this._scrollOutput();
            this.$nextTick(() => this.$refs.termInput?.focus());
        },

        clearOutput() {
            this.outputLines = [];
        },

        _scrollOutput() {
            this.$nextTick(() => {
                const el = this.$refs.termOutput;
                if (el) el.scrollTop = el.scrollHeight;
            });
        },
    };
}


// ─── Sandbox Delete (global, used by subnav) ────────────────────────────────

async function deleteSandbox(name) {
    const confirmed = await showConfirm(
        `Delete sandbox "${name}"? This cannot be undone.`,
        { icon: 'trash', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Delete' }
    );
    if (!confirmed) return;
    try {
        await apiFetch(`${API}/sandboxes/${name}`, { method: 'DELETE' });
        showToast(`Sandbox "${name}" deleted.`, 'success');
        navigateTo(gwUrl('/sandboxes'));
    } catch (e) {
        showToast(`Delete failed: ${e.message}`, 'danger');
    }
}
