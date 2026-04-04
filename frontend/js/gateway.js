/**
 * Shoreguard v0.4 — Multi-Gateway Management (Alpine.js)
 * Alpine components for gateway list, detail, register, and inference config.
 */

// Shared status icon/label maps
const _gwStatusIcons = {
    connected: 'circle-fill', running: 'circle-fill',
    unreachable: 'exclamation-circle', stopped: 'stop-circle', offline: 'circle',
};
const _gwStatusLabels = {
    connected: 'Connected', running: 'Running',
    unreachable: 'Unreachable', stopped: 'Stopped', offline: 'Offline',
};

// Cached inference providers
let _knownProviders = [];

// ─── Gateway List Component ────────────────────────────────────────────────

function gatewayList() {
    return {
        gateways: [],
        loading: true,
        error: null,

        statusIcon(s) { return _gwStatusIcons[s || 'offline'] || 'circle'; },
        statusLabel(s) { return _gwStatusLabels[s || 'offline'] || (s || 'offline'); },

        async load() {
            this.loading = true;
            this.error = null;
            try {
                this.gateways = await apiFetch(`${API_GLOBAL}/gateway/list`);
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        async unregister(name) {
            const confirmed = await showConfirm(
                `Unregister gateway "${name}"? This removes it from Shoreguard but does not affect the running gateway.`,
                { icon: 'trash', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Unregister' }
            );
            if (!confirmed) return;
            try {
                const result = await apiFetch(`${API_GLOBAL}/gateway/${name}`, { method: 'DELETE' });
                if (result.success) {
                    showToast(`Gateway "${name}" unregistered.`, 'success');
                    Alpine.store('health').check();
                    this.load();
                } else {
                    showToast(`Failed: ${result.error}`, 'danger');
                }
            } catch (e) {
                showToast(`Error: ${e.message}`, 'danger');
            }
        },
    };
}

// ─── Gateway Register Component ────────────────────────────────────────────

function gatewayRegister() {
    return {
        form: { name: '', endpoint: '', scheme: 'https', auth_mode: 'mtls', gpu: false, caFile: null, certFile: null, keyFile: null, description: '' },
        labelRows: [],
        newLabelKey: '',
        newLabelVal: '',
        submitting: false,
        output: '',

        resetForm() {
            this.form = { name: '', endpoint: '', scheme: 'https', auth_mode: 'mtls', gpu: false, caFile: null, certFile: null, keyFile: null, description: '' };
            this.labelRows = [];
            this.newLabelKey = '';
            this.newLabelVal = '';
            this.output = '';
            if (this.$refs.caInput) this.$refs.caInput.value = '';
            if (this.$refs.certInput) this.$refs.certInput.value = '';
            if (this.$refs.keyInput) this.$refs.keyInput.value = '';
        },

        addLabel() {
            const key = this.newLabelKey.trim();
            const val = this.newLabelVal.trim();
            if (!key) return;
            if (this.labelRows.some(r => r.key === key)) return;
            if (this.labelRows.length >= 20) return;
            this.labelRows.push({ key, val });
            this.newLabelKey = '';
            this.newLabelVal = '';
        },

        removeLabel(key) {
            this.labelRows = this.labelRows.filter(r => r.key !== key);
        },

        async submit() {
            if (!this.form.name.trim()) { this.output = '<div class="text-danger small">Name is required.</div>'; return; }
            if (!this.form.endpoint.trim()) { this.output = '<div class="text-danger small">Endpoint is required.</div>'; return; }

            this.submitting = true;
            this.output = '<div class="text-muted small"><div class="spinner-border spinner-border-sm me-2"></div>Registering gateway...</div>';

            try {
                const body = {
                    name: this.form.name.trim(),
                    endpoint: this.form.endpoint.trim(),
                    scheme: this.form.scheme,
                    auth_mode: this.form.auth_mode,
                    metadata: { gpu: this.form.gpu },
                };

                const desc = this.form.description.trim();
                if (desc) body.description = desc;

                if (this.labelRows.length > 0) {
                    const labels = {};
                    for (const r of this.labelRows) labels[r.key] = r.val;
                    body.labels = labels;
                }

                if (this.form.caFile) body.ca_cert = await readFileAsBase64(this.form.caFile);
                if (this.form.certFile) body.client_cert = await readFileAsBase64(this.form.certFile);
                if (this.form.keyFile) body.client_key = await readFileAsBase64(this.form.keyFile);

                await apiFetch(`${API_GLOBAL}/gateway/register`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });

                this.output = '<div class="text-success small"><i class="bi bi-check-circle me-1"></i>Gateway registered!</div>';
                showToast(`Gateway "${body.name}" registered.`, 'success');
                bootstrap.Modal.getInstance(document.getElementById('registerGatewayModal'))?.hide();
                Alpine.store('health').check();
                // Refresh the gateway list
                window.dispatchEvent(new CustomEvent('gateway-registered'));
            } catch (e) {
                this.output = `<div class="text-danger small">${escapeHtml(e.message)}</div>`;
            } finally {
                this.submitting = false;
            }
        },
    };
}

// ─── Gateway Detail Component ──────────────────────────────────────────────

function gatewayDetail(name) {
    return {
        name,
        gw: null,
        loading: true,
        error: null,
        actionOutput: '',
        actionClass: '',
        acting: false,
        metaForm: { description: '' },
        metaLabels: [],
        newMetaKey: '',
        newMetaVal: '',
        metaSaving: false,
        metaOutput: '',

        statusIcon(s) { return _gwStatusIcons[s || 'offline'] || 'circle'; },
        statusLabel(s) { return _gwStatusLabels[s || 'offline'] || (s || 'offline'); },
        gwUrl(path) { return `/gateways/${name}${path}`; },

        async load() {
            this.loading = true;
            this.error = null;
            try {
                const gateways = await apiFetch(`${API_GLOBAL}/gateway/list`);
                this.gw = gateways.find(g => g.name === name) || null;
                if (!this.gw) return;

                // Populate metadata form from gateway data
                this.metaForm.description = this.gw.description || '';
                this.metaLabels = Object.entries(this.gw.labels || {}).map(([k, v]) => ({ key: k, val: v }));

                // Cache providers for inference config
                if (_knownProviders.length === 0 && this.gw.connected) {
                    try { _knownProviders = await apiFetch(`${API}/providers/inference-providers`); } catch {}
                }
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        async testConnection() {
            this.actionOutput = '<div class="spinner-border spinner-border-sm me-2"></div>Testing connection...';
            this.actionClass = '';
            try {
                const result = await apiFetch(`${API_GLOBAL}/gateway/${name}/test-connection`, { method: 'POST' });
                if (result.success) {
                    this.actionOutput = `Connected! ${result.version ? 'v' + escapeHtml(result.version) : ''} (${escapeHtml(result.health_status || 'ok')})`;
                    this.actionClass = 'log-info';
                    showToast('Connection successful.', 'success');
                    setTimeout(() => this.load(), SG.config.actionRefreshDelay);
                } else {
                    this.actionOutput = `Connection failed: ${escapeHtml(result.error || 'Unknown error')}`;
                    this.actionClass = 'log-error';
                    showToast('Connection failed.', 'danger');
                }
            } catch (e) {
                this.actionOutput = `Error: ${escapeHtml(e.message)}`;
                this.actionClass = 'log-error';
            }
        },

        async startGateway() {
            this.acting = true;
            this.actionOutput = '<div class="spinner-border spinner-border-sm me-2"></div>Starting gateway...';
            this.actionClass = '';
            try {
                const result = await apiFetch(`${API_GLOBAL}/gateway/${name}/start`, { method: 'POST' });
                if (result.success) {
                    this.actionOutput = `Gateway started. ${escapeHtml(result.output || '')}`;
                    this.actionClass = 'log-info';
                    showToast('Gateway started.', 'success');
                    Alpine.store('health').check();
                    setTimeout(() => this.load(), SG.config.actionRefreshDelay);
                } else {
                    this.actionOutput = `Start failed: ${escapeHtml(result.error || 'Unknown error')}`;
                    this.actionClass = 'log-error';
                }
            } catch (e) {
                this.actionOutput = `Error: ${escapeHtml(e.message)}`;
                this.actionClass = 'log-error';
            } finally {
                this.acting = false;
            }
        },

        async stopGateway() {
            this.acting = true;
            this.actionOutput = '<div class="spinner-border spinner-border-sm me-2"></div>Stopping gateway...';
            this.actionClass = '';
            try {
                const result = await apiFetch(`${API_GLOBAL}/gateway/${name}/stop`, { method: 'POST' });
                if (result.success) {
                    this.actionOutput = 'Gateway stopped.';
                    this.actionClass = 'log-info';
                    showToast('Gateway stopped.', 'success');
                    Alpine.store('health').check();
                    setTimeout(() => this.load(), SG.config.actionRefreshDelay);
                } else {
                    this.actionOutput = `Stop failed: ${escapeHtml(result.error || 'Unknown error')}`;
                    this.actionClass = 'log-error';
                }
            } catch (e) {
                this.actionOutput = `Error: ${escapeHtml(e.message)}`;
                this.actionClass = 'log-error';
            } finally {
                this.acting = false;
            }
        },

        async restartGateway() {
            this.acting = true;
            this.actionOutput = '<div class="spinner-border spinner-border-sm me-2"></div>Restarting gateway...';
            this.actionClass = '';
            try {
                const result = await apiFetch(`${API_GLOBAL}/gateway/${name}/restart`, { method: 'POST' });
                if (result.success) {
                    this.actionOutput = `Gateway restarted. ${escapeHtml(result.output || '')}`;
                    this.actionClass = 'log-info';
                    showToast('Gateway restarted.', 'success');
                    Alpine.store('health').check();
                    setTimeout(() => this.load(), SG.config.actionRefreshDelay);
                } else {
                    this.actionOutput = `Restart failed: ${escapeHtml(result.error || 'Unknown error')}`;
                    this.actionClass = 'log-error';
                }
            } catch (e) {
                this.actionOutput = `Error: ${escapeHtml(e.message)}`;
                this.actionClass = 'log-error';
            } finally {
                this.acting = false;
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
            this.metaSaving = true;
            this.metaOutput = '';
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
                await apiFetch(`${API_GLOBAL}/gateway/${this.name}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                this.metaOutput = '<span class="text-success"><i class="bi bi-check-circle me-1"></i>Saved</span>';
                setTimeout(() => { this.metaOutput = ''; }, 2000);
                this.load();
            } catch (e) {
                this.metaOutput = `<span class="text-danger">${escapeHtml(e.message)}</span>`;
            } finally {
                this.metaSaving = false;
            }
        },

        async unregister() {
            const confirmed = await showConfirm(
                `Unregister gateway "${name}"? This removes it from Shoreguard but does not affect the running gateway.`,
                { icon: 'trash', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Unregister' }
            );
            if (!confirmed) return;
            try {
                const result = await apiFetch(`${API_GLOBAL}/gateway/${name}`, { method: 'DELETE' });
                if (result.success) {
                    showToast(`Gateway "${name}" unregistered.`, 'success');
                    Alpine.store('health').check();
                    navigateTo('/gateways');
                } else {
                    showToast(`Failed: ${result.error}`, 'danger');
                }
            } catch (e) {
                showToast(`Error: ${e.message}`, 'danger');
            }
        },
    };
}

// ─── Inference Config Component ────────────────────────────────────────────

function inferenceConfig() {
    return {
        provider: '',
        modelId: '',
        timeoutSecs: 0,
        providers: _knownProviders,
        placeholder: 'model-id',
        loading: false,
        loaded: false,
        saving: false,
        saveOutput: '',

        get currentProvider() {
            return this.providers.find(p => p.name === this.provider) || null;
        },

        async load() {
            this.loading = true;
            this.loaded = true;
            // Ensure providers are loaded
            if (_knownProviders.length === 0) {
                try { _knownProviders = await apiFetch(`${API}/providers/inference-providers`); } catch {}
            }
            this.providers = _knownProviders;
            try {
                const config = await apiFetch(`${API}/inference`);
                this.provider = config.provider_name || '';
                this.modelId = config.model_id || '';
                this.timeoutSecs = config.timeout_secs || 0;
                const cp = this.currentProvider;
                if (cp) this.placeholder = cp.placeholder;
            } catch {
                this.provider = '';
                this.modelId = '';
            } finally {
                this.loading = false;
            }
        },

        onProviderChange() {
            const cp = this.currentProvider;
            if (cp) {
                this.placeholder = cp.placeholder;
                if (!this.modelId) this.modelId = cp.placeholder;
            }
        },

        async save() {
            if (!this.provider) { this.saveOutput = '<div class="text-danger small">Select a provider.</div>'; return; }
            if (!this.modelId) { this.saveOutput = '<div class="text-danger small">Enter a model ID.</div>'; return; }

            this.saving = true;
            this.saveOutput = '<div class="text-muted small"><div class="spinner-border spinner-border-sm me-2"></div>Saving...</div>';
            try {
                await apiFetch(`${API}/inference`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ provider_name: this.provider, model_id: this.modelId, verify: false, timeout_secs: this.timeoutSecs }),
                });
                this.saveOutput = '<div class="text-success small"><i class="bi bi-check-circle me-1"></i>Provider configured.</div>';
                showToast('Inference provider saved.', 'success');
            } catch (e) {
                this.saveOutput = `<div class="text-danger small"><i class="bi bi-x-circle me-1"></i>${escapeHtml(e.message)}</div>`;
                showToast('Failed to save provider.', 'danger');
            } finally {
                this.saving = false;
            }
        },
    };
}

// ─── Shared Helpers ────────────────────────────────────────────────────────

function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result.split(',')[1]);
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });
}
