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
                const resp = await apiFetch(`${API}/sandboxes`);
                this.sandboxes = Array.isArray(resp) ? resp : (resp.items || []);
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
        securityFlaggedCount: 0,
        lastAnalyzedAtMs: 0,
        networkCount: 0,
        policy: null,
        metaDescription: '',
        metaLabels: [],
        newMetaKey: '',
        get canAddMeta() { return this.newMetaKey.trim().length > 0; },
        newMetaVal: '',
        saving: false,
        saveOutput: '',
        wsState: 'connecting',
        // Attached providers (M37 / OpenShell PR #1242)
        attachedProviders: [],
        availableProviders: [],
        attachProviderName: '',
        attachBusy: false,
        attachError: '',

        async init() {
            await this.load();
        },

        onWsState(ev) {
            if (ev.detail && ev.detail.sandboxName === this.sandboxName) {
                this.wsState = ev.detail.state;
            }
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                const [sb, draft, policyData, attached] = await Promise.all([
                    apiFetch(`${API}/sandboxes/${name}`),
                    // Full draft (chunks + rolling_summary + last_analyzed_at_ms)
                    // rather than /approvals/pending: lets us derive the three
                    // overview-card counters in a single call.
                    apiFetch(`${API}/sandboxes/${name}/approvals`).catch(() => null),
                    apiFetch(`${API}/sandboxes/${name}/policy`).catch(() => null),
                    apiFetch(`${API}/sandboxes/${name}/providers`).catch(() => []),
                ]);

                this.sandbox = sb;
                this.metaDescription = sb.description || '';
                this.metaLabels = Object.entries(sb.labels || {}).map(([k, v]) => ({ key: k, val: v }));
                this.attachedProviders = Array.isArray(attached) ? attached : [];
                const chunks = (draft && draft.chunks) || [];
                const pending = chunks.filter(c => c.status === 'pending');
                this.pendingCount = pending.length;
                this.securityFlaggedCount = pending.filter(c => c.security_notes && c.security_notes.trim()).length;
                this.lastAnalyzedAtMs = (draft && draft.last_analyzed_at_ms) || 0;
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
            const desc = this.metaDescription.trim();
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

        // ─── Attached Providers (M37, OpenShell PR #1242) ────────────────────
        async loadAvailableProviders() {
            // Lazy-load on first attach attempt; the global provider list can
            // be larger than the attached-list, so we only fetch when the user
            // actually opens the picker.
            if (this.availableProviders.length > 0) return;
            try {
                const resp = await apiFetch(`${API}/providers`);
                this.availableProviders = Array.isArray(resp) ? resp : (resp.items || []);
            } catch (e) {
                this.attachError = `Failed to load providers: ${e.message}`;
            }
        },

        get attachableProviders() {
            const attachedNames = new Set(this.attachedProviders.map(p => p.name));
            return this.availableProviders.filter(p => !attachedNames.has(p.name));
        },

        async attachProvider() {
            const provName = (this.attachProviderName || '').trim();
            if (!provName) return;
            this.attachBusy = true;
            this.attachError = '';
            try {
                const resp = await apiFetch(
                    `${API}/sandboxes/${this.sandboxName}/providers`,
                    {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ provider_name: provName }),
                    },
                );
                if (resp.attached) {
                    showToast(`Provider "${provName}" attached.`, 'success');
                } else {
                    showToast(`Provider "${provName}" was already attached.`, 'info');
                }
                this.attachProviderName = '';
                await this.refreshAttachedProviders();
            } catch (e) {
                this.attachError = e.message;
            } finally {
                this.attachBusy = false;
            }
        },

        async detachProvider(providerName) {
            const confirmed = await showConfirm(
                `Detach provider "${providerName}" from this sandbox?`,
                { icon: 'plug', btnLabel: 'Detach' },
            );
            if (!confirmed) return;
            try {
                const resp = await apiFetch(
                    `${API}/sandboxes/${this.sandboxName}/providers/${providerName}`,
                    { method: 'DELETE' },
                );
                if (resp.detached) {
                    showToast(`Provider "${providerName}" detached.`, 'success');
                } else {
                    showToast(`Provider "${providerName}" was not attached.`, 'info');
                }
                await this.refreshAttachedProviders();
            } catch (e) {
                showToast(`Detach failed: ${e.message}`, 'danger');
            }
        },

        async refreshAttachedProviders() {
            try {
                const resp = await apiFetch(`${API}/sandboxes/${this.sandboxName}/providers`);
                this.attachedProviders = Array.isArray(resp) ? resp : [];
            } catch (e) {
                this.attachedProviders = [];
            }
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
                const response = await apiFetch(`${API}/sandboxes/${this.sandboxName}/exec`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ command: cmd }),
                });

                const op = await pollOperation(response.operation_id);
                if (op.status === 'failed') {
                    this.outputLines.push({ text: `Error: ${op.error || 'Command failed'}`, cls: 'log-error' });
                } else {
                    const result = op.result;
                    if (result.stdout) {
                        this.outputLines.push({ text: result.stdout, css: 'white-space:pre-wrap' });
                    }
                    if (result.stderr) {
                        this.outputLines.push({ text: result.stderr, css: 'white-space:pre-wrap', cls: 'log-error' });
                    }
                    if (result.exit_code !== 0) {
                        this.outputLines.push({ text: `exit code: ${result.exit_code}`, cls: 'log-error' });
                    }
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


// ─── Alpine.data registrations ──────────────────────────────────────────────

// ─── Sandbox subnav component (CSP-strict @click dispatcher) ────────────────
// Used by components/sandbox_nav.html — reads sandbox name from data-attr so
// the delete button can dispatch via a component method instead of inline onclick.
function sandboxNav() {
    return {
        sandboxName: '',
        init() { this.sandboxName = this.$el.dataset.sandboxName || ''; },
        del() { if (this.sandboxName) deleteSandbox(this.sandboxName); },
    };
}

document.addEventListener('alpine:init', () => {
    Alpine.data('sandboxList', sandboxList);
    Alpine.data('sandboxDetail', sandboxDetail);
    Alpine.data('terminalPage', terminalPage);
    Alpine.data('sandboxNav', sandboxNav);
});


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
