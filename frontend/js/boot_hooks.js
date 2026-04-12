/**
 * Boot hooks page — Alpine.js component (M22).
 *
 * CRUD + manual run for per-sandbox pre/post-create hooks. Hooks are
 * grouped by phase, displayed in execution order, and can be toggled,
 * reordered, edited, deleted, or run on demand.
 */

/* global sgFetch, bootstrap */

const PHASES = ['pre_create', 'post_create'];

function emptyEditing(phase) {
    return {
        id: null,
        name: '',
        phase: phase || 'pre_create',
        command: '',
        workdir: '',
        envText: '',
        timeout_seconds: 30,
        enabled: true,
        continue_on_failure: false,
    };
}

function envTextToObject(text) {
    const out = {};
    if (!text) return out;
    for (const rawLine of text.split('\n')) {
        const line = rawLine.trim();
        if (!line || line.startsWith('#')) continue;
        const eq = line.indexOf('=');
        if (eq <= 0) continue;
        const k = line.slice(0, eq).trim();
        const v = line.slice(eq + 1);
        if (k) out[k] = v;
    }
    return out;
}

function envObjectToText(env) {
    if (!env) return '';
    return Object.entries(env)
        .map(([k, v]) => `${k}=${v}`)
        .join('\n');
}

function hooksPage(gatewayName, sandboxName) {
    return {
        gatewayName,
        sandboxName,
        phases: PHASES,
        loading: false,
        loaded: false,
        saving: false,
        items: [],
        editing: emptyEditing('pre_create'),
        editorError: '',
        editorModal: null,

        get baseUrl() {
            return `/api/gateways/${encodeURIComponent(this.gatewayName)}/sandboxes/${encodeURIComponent(this.sandboxName)}/hooks`;
        },

        get grouped() {
            const out = { pre_create: [], post_create: [] };
            for (const hook of this.items) {
                if (out[hook.phase]) out[hook.phase].push(hook);
            }
            for (const phase of PHASES) {
                out[phase].sort((a, b) => a.order - b.order || a.id - b.id);
            }
            return out;
        },

        phaseLabel(phase) {
            return phase === 'pre_create' ? 'Pre-create' : 'Post-create';
        },

        async init() {
            this.editorModal = new bootstrap.Modal(this.$refs.editorModal);
            await this.loadHooks();
        },

        async loadHooks() {
            this.loading = true;
            try {
                const resp = await sgFetch(this.baseUrl);
                if (!resp.ok) {
                    console.error('Failed to load hooks:', resp.status);
                    return;
                }
                const data = await resp.json();
                this.items = data.items || [];
                this.loaded = true;
            } finally {
                this.loading = false;
            }
        },

        openCreate(phase) {
            this.editing = emptyEditing(phase);
            this.editorError = '';
            this.editorModal.show();
        },

        openEdit(hook) {
            this.editing = {
                id: hook.id,
                name: hook.name,
                phase: hook.phase,
                command: hook.command,
                workdir: hook.workdir || '',
                envText: envObjectToText(hook.env),
                timeout_seconds: hook.timeout_seconds,
                enabled: hook.enabled,
                continue_on_failure: hook.continue_on_failure,
            };
            this.editorError = '';
            this.editorModal.show();
        },

        async saveEditing() {
            this.editorError = '';
            this.saving = true;
            try {
                const env = envTextToObject(this.editing.envText);
                if (this.editing.id) {
                    const body = {
                        command: this.editing.command,
                        workdir: this.editing.workdir,
                        env,
                        timeout_seconds: this.editing.timeout_seconds,
                        enabled: this.editing.enabled,
                        continue_on_failure: this.editing.continue_on_failure,
                    };
                    const resp = await sgFetch(`${this.baseUrl}/${this.editing.id}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body),
                    });
                    if (!resp.ok) {
                        const err = await resp.json().catch(() => ({}));
                        this.editorError = err.detail || `HTTP ${resp.status}`;
                        return;
                    }
                } else {
                    const body = {
                        name: this.editing.name,
                        phase: this.editing.phase,
                        command: this.editing.command,
                        workdir: this.editing.workdir,
                        env,
                        timeout_seconds: this.editing.timeout_seconds,
                        enabled: this.editing.enabled,
                        continue_on_failure: this.editing.continue_on_failure,
                    };
                    const resp = await sgFetch(this.baseUrl, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body),
                    });
                    if (!resp.ok) {
                        const err = await resp.json().catch(() => ({}));
                        this.editorError = err.detail || `HTTP ${resp.status}`;
                        return;
                    }
                }
                this.editorModal.hide();
                await this.loadHooks();
            } finally {
                this.saving = false;
            }
        },

        async toggleEnabled(hook) {
            const next = !hook.enabled;
            const resp = await sgFetch(`${this.baseUrl}/${hook.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: next }),
            });
            if (!resp.ok) {
                console.error('Failed to toggle hook:', resp.status);
                return;
            }
            await this.loadHooks();
        },

        async deleteHook(hook) {
            if (!confirm(`Delete hook '${hook.name}'?`)) return;
            const resp = await sgFetch(`${this.baseUrl}/${hook.id}`, { method: 'DELETE' });
            if (!resp.ok) {
                console.error('Failed to delete hook:', resp.status);
                return;
            }
            await this.loadHooks();
        },

        async runHook(hook) {
            const resp = await sgFetch(`${this.baseUrl}/${hook.id}/run`, { method: 'POST' });
            if (!resp.ok) {
                console.error('Failed to run hook:', resp.status);
                return;
            }
            await this.loadHooks();
        },

        async moveUp(hook) {
            await this.move(hook, -1);
        },

        async moveDown(hook) {
            await this.move(hook, +1);
        },

        async move(hook, delta) {
            const phaseHooks = this.grouped[hook.phase];
            const idx = phaseHooks.findIndex((h) => h.id === hook.id);
            const target = idx + delta;
            if (idx < 0 || target < 0 || target >= phaseHooks.length) return;
            const reordered = phaseHooks.slice();
            const [taken] = reordered.splice(idx, 1);
            reordered.splice(target, 0, taken);
            const resp = await sgFetch(`${this.baseUrl}/reorder`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    phase: hook.phase,
                    hook_ids: reordered.map((h) => h.id),
                }),
            });
            if (!resp.ok) {
                console.error('Failed to reorder hooks:', resp.status);
                return;
            }
            await this.loadHooks();
        },
    };
}

window.hooksPage = hooksPage;
