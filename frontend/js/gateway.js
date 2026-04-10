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
                const resp = await apiFetch(`${API_GLOBAL}/gateway/list`);
                this.gateways = Array.isArray(resp) ? resp : (resp.items || []);
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

// ─── Gateway Register Page Component ──────────────────────────────────────
// Full-page version (used by /gateways/new) — redirects on success.

function gatewayRegisterPage() {
    return {
        fName: '', fEndpoint: '', fScheme: 'https', fAuthMode: 'mtls', fGpu: false, fCaFile: null, fCertFile: null, fKeyFile: null, fDescription: '',
        labelRows: [],
        newLabelKey: '',
        newLabelVal: '',
        submitting: false,
        output: '',

        get canAddLabel() { return this.newLabelKey.trim().length > 0; },

        pickCaFile(e) { this.fCaFile = e.target.files[0]; },
        pickCertFile(e) { this.fCertFile = e.target.files[0]; },
        pickKeyFile(e) { this.fKeyFile = e.target.files[0]; },

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
            if (!this.fName.trim()) { this.output = '<div class="text-danger small">Name is required.</div>'; return; }
            if (!this.fEndpoint.trim()) { this.output = '<div class="text-danger small">Endpoint is required.</div>'; return; }

            this.submitting = true;
            this.output = '<div class="text-muted small"><div class="spinner-border spinner-border-sm me-2"></div>Registering gateway...</div>';

            try {
                const body = {
                    name: this.fName.trim(),
                    endpoint: this.fEndpoint.trim(),
                    scheme: this.fScheme,
                    auth_mode: this.fAuthMode,
                    metadata: { gpu: this.fGpu },
                };

                const desc = this.fDescription.trim();
                if (desc) body.description = desc;

                if (this.labelRows.length > 0) {
                    const labels = {};
                    for (const r of this.labelRows) labels[r.key] = r.val;
                    body.labels = labels;
                }

                if (this.fCaFile) body.ca_cert = await readFileAsBase64(this.fCaFile);
                if (this.fCertFile) body.client_cert = await readFileAsBase64(this.fCertFile);
                if (this.fKeyFile) body.client_key = await readFileAsBase64(this.fKeyFile);

                await apiFetch(`${API_GLOBAL}/gateway/register`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });

                showToast(`Gateway "${body.name}" registered.`, 'success');
                navigateTo('/gateways');
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
        metaDescription: '',
        metaLabels: [],
        newMetaKey: '',
        newMetaVal: '',
        saving: false,
        saveOutput: '',

        get canAddMeta() { return this.newMetaKey.trim().length > 0; },

        statusIcon(s) { return _gwStatusIcons[s || 'offline'] || 'circle'; },
        statusLabel(s) { return _gwStatusLabels[s || 'offline'] || (s || 'offline'); },
        gwUrl(path) { return `/gateways/${name}${path}`; },

        // ── CSP-strict getters ─────────────────────────────────────────────
        // The Alpine CSP expression parser can't evaluate `gw?.xxx` or
        // `gw.xxx === 'y'` directly, so the template reads the data it
        // needs through these single-variable getters.
        get gwStatus() { return this.gw?.status || ''; },
        get gwConnected() { return this.gw?.status === 'connected'; },
        get gwNotConnected() { return this.gw?.status !== 'connected'; },
        get gwVersion() { return this.gw?.version || ''; },
        get gwDescription() { return this.gw?.description || '—'; },
        get gwAuthMode() { return this.gw?.auth_mode; },
        get gwRegisteredAt() { return this.gw?.registered_at; },
        get gwLastSeen() { return this.gw?.last_seen; },
        get gwEndpointDisplay() {
            return (this.gw?.scheme || 'https') + '://' + (this.gw?.endpoint || '—');
        },
        get gwStatusBadgeClass() {
            return (SG.badges.gateway || {})[this.gw?.status] || 'text-bg-secondary';
        },
        get gwStatusIconClass() { return 'bi-' + this.statusIcon(this.gw?.status); },
        get gwStatusLabelText() { return this.statusLabel(this.gw?.status); },
        get gwSandboxesUrl() { return this.gw?.status === 'connected' ? this.gwUrl('/sandboxes') : null; },
        get gwProvidersUrl() { return this.gw?.status === 'connected' ? this.gwUrl('/providers') : null; },
        get gwWizardUrl() { return this.gw?.status === 'connected' ? this.gwUrl('/wizard') : null; },

        async load() {
            this.loading = true;
            this.error = null;
            try {
                const gateways = await apiFetch(`${API_GLOBAL}/gateway/list`);
                this.gw = gateways.find(g => g.name === name) || null;
                if (!this.gw) return;

                // Populate metadata form from gateway data
                this.metaDescription = this.gw.description || '';
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

        async saveAll() {
            this.saving = true;
            this.saveOutput = '';
            const errors = [];

            // Save metadata (always)
            const metaBody = {};
            const desc = this.metaDescription.trim();
            metaBody.description = desc || null;
            if (this.metaLabels.length > 0) {
                const labels = {};
                for (const r of this.metaLabels) labels[r.key] = r.val;
                metaBody.labels = labels;
            } else {
                metaBody.labels = null;
            }
            try {
                await apiFetch(`${API_GLOBAL}/gateway/${this.name}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(metaBody),
                });
            } catch (e) {
                errors.push(`Metadata: ${e.message}`);
            }

            // Save inference (only if connected and provider selected)
            const inferenceEl = this.$root.querySelector('[x-data*="inferenceConfig"]');
            if (inferenceEl && this.gw?.connected) {
                const inferenceScope = Alpine.$data(inferenceEl);
                if (inferenceScope?.provider) {
                    try {
                        await apiFetch(`${API}/inference`, {
                            method: 'PUT',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                provider_name: inferenceScope.provider,
                                model_id: inferenceScope.modelId,
                                verify: false,
                                timeout_secs: inferenceScope.timeoutSecs,
                            }),
                        });
                    } catch (e) {
                        errors.push(`Inference: ${e.message}`);
                    }
                }
            }

            if (errors.length === 0) {
                this.saveOutput = '<span class="text-success"><i class="bi bi-check-circle me-1"></i>Saved</span>';
                setTimeout(() => { this.saveOutput = ''; }, 2000);
                this.load();
            } else {
                this.saveOutput = `<span class="text-danger">${errors.map(escapeHtml).join('; ')}</span>`;
            }
            this.saving = false;
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

        maybeLoad(gw) {
            if (gw && gw.connected && !this.loaded) this.load();
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

// ─── Gateway Settings Component ────────────────────────────────────────────

function gatewaySettings() {
    return {
        ocsfLoggingEnabled: false,
        loading: false,
        loaded: false,
        saving: false,
        gwName: '',

        maybeLoad(gw) {
            if (gw && gw.connected && !this.loaded) {
                this.gwName = gw.name;
                this.load();
            }
        },

        async load() {
            this.loading = true;
            this.loaded = true;
            try {
                const config = await apiFetch(`${API_GLOBAL}/gateway/${this.gwName}/settings`);
                const settings = (config && config.settings) || {};
                this.ocsfLoggingEnabled = settings.ocsf_logging_enabled === true;
            } catch {
                this.ocsfLoggingEnabled = false;
            } finally {
                this.loading = false;
            }
        },

        async onOcsfToggle() {
            const previous = !this.ocsfLoggingEnabled;
            this.saving = true;
            try {
                await apiFetch(
                    `${API_GLOBAL}/gateway/${this.gwName}/settings/ocsf_logging_enabled`,
                    {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ value: this.ocsfLoggingEnabled }),
                    },
                );
                showToast(
                    `OCSF logging ${this.ocsfLoggingEnabled ? 'enabled' : 'disabled'}.`,
                    'success',
                );
            } catch (e) {
                this.ocsfLoggingEnabled = previous;
                showToast(`Failed to update setting: ${e.message}`, 'danger');
            } finally {
                this.saving = false;
            }
        },
    };
}


// ─── Alpine.data registrations ─────────────────────────────────────────────

document.addEventListener('alpine:init', () => {
    Alpine.data('gatewayRegisterPage', gatewayRegisterPage);
    Alpine.data('gatewayDetail', gatewayDetail);
    Alpine.data('inferenceConfig', inferenceConfig);
    Alpine.data('gatewaySettings', gatewaySettings);
    // Spread-merge factory replacing inline `{ ...gatewayList(), ...sortableTable('name') }`.
    Alpine.data('gatewaysList', () => ({ ...gatewayList(), ...sortableTable('name') }));
});


// ─── Shared Helpers ────────────────────────────────────────────────────────

function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result.split(',')[1]);
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });
}
