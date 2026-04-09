/**
 * Shoreguard — Network Rule Detail & Editor (Alpine.js)
 */

function ruleDetailPage(sandboxName, ruleKey) {
    return {
        sandboxName,
        ruleKey,
        loading: true,
        error: '',
        isNew: ruleKey === '_new',
        editing: false,
        rule: null,

        // Editor state
        editKey: ruleKey === '_new' ? '' : ruleKey,
        editName: '',
        editBinaries: '',
        endpoints: [],
        epCounter: 0,
        l7Counter: 0,

        async init() {
            if (this.isNew) {
                this.editing = true;
                this.loading = false;
                this.endpoints = [this._emptyEndpoint()];
                return;
            }
            await this.load();
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                const data = await apiFetch(`${API}/sandboxes/${sandboxName}/policy`);
                const policy = data.policy;

                if (!policy || !policy.network_policies || !policy.network_policies[this.ruleKey]) {
                    this.error = `Rule "${this.ruleKey}" not found.`;
                    return;
                }

                this.rule = policy.network_policies[this.ruleKey];
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        switchToEdit() {
            if (!this.rule) return;
            this.editKey = this.ruleKey;
            this.editName = this.rule.name || '';
            this.editBinaries = (this.rule.binaries || []).map(b => b.path).join('\n');
            this.endpoints = (this.rule.endpoints || []).map(ep => ({
                id: this.epCounter++,
                host: ep.host || '',
                port: ep.port || 443,
                protocol: ep.protocol || '',
                tls: ep.tls || '',
                enforcement: ep.enforcement || '',
                access: ep.access || '',
                rules: (ep.rules || []).map(r => this._mapL7Rule(r)),
            }));
            this.editing = true;
        },

        cancelEdit() {
            if (this.isNew) {
                navigateTo(gwUrl(`/sandboxes/${sandboxName}/policy`));
            } else {
                this.editing = false;
            }
        },

        addEndpoint() {
            this.endpoints.push(this._emptyEndpoint());
        },

        removeEndpoint(id) {
            this.endpoints = this.endpoints.filter(ep => ep.id !== id);
        },

        _emptyEndpoint() {
            return {
                id: this.epCounter++,
                host: '',
                port: 443,
                protocol: '',
                tls: '',
                enforcement: '',
                access: '',
                rules: [],
            };
        },

        _mapL7Rule(r) {
            const allow = r.allow || {};
            const query = Object.entries(allow.query || {}).map(([param, matcher]) => ({
                param,
                type: matcher.glob ? 'glob' : 'any',
                value: matcher.glob || (matcher.any || []).join(', '),
            }));
            return {
                id: this.l7Counter++,
                method: allow.method || '',
                path: allow.path || '',
                command: allow.command || '',
                query,
            };
        },

        addL7Rule(ep) {
            ep.rules.push({
                id: this.l7Counter++,
                method: '',
                path: '',
                command: '',
                query: [],
            });
        },

        removeL7Rule(ep, ruleId) {
            ep.rules = ep.rules.filter(r => r.id !== ruleId);
        },

        addQueryMatcher(rule) {
            rule.query.push({ param: '', type: 'glob', value: '' });
        },

        removeQueryMatcher(rule, index) {
            rule.query.splice(index, 1);
        },

        async save() {
            const key = this.isNew ? this.editKey.trim() : this.ruleKey;
            const name = this.editName.trim();
            const eps = this.endpoints
                .filter(ep => ep.host.trim())
                .map(ep => {
                    const o = { host: ep.host.trim(), port: parseInt(ep.port) || 443 };
                    if (ep.protocol) o.protocol = ep.protocol;
                    if (ep.tls) o.tls = ep.tls;
                    if (ep.enforcement) o.enforcement = ep.enforcement;
                    if (ep.access) o.access = ep.access;
                    const rules = (ep.rules || [])
                        .filter(r => r.method || r.path || r.command)
                        .map(r => {
                            const allow = {};
                            if (r.method) allow.method = r.method;
                            if (r.path) allow.path = r.path;
                            if (r.command) allow.command = r.command;
                            const query = {};
                            (r.query || []).filter(qm => qm.param.trim()).forEach(qm => {
                                if (qm.type === 'glob') {
                                    query[qm.param.trim()] = { glob: qm.value.trim() };
                                } else {
                                    query[qm.param.trim()] = { any: qm.value.split(',').map(v => v.trim()).filter(Boolean) };
                                }
                            });
                            if (Object.keys(query).length) allow.query = query;
                            return { allow };
                        });
                    if (rules.length) o.rules = rules;
                    return o;
                });
            const binaries = this.editBinaries.trim()
                ? this.editBinaries.split('\n').map(p => ({ path: p.trim() })).filter(b => b.path)
                : [];

            if (!key) { showToast('Rule key is required.', 'warning'); return; }
            if (eps.length === 0) { showToast('At least one endpoint is required.', 'warning'); return; }

            try {
                await apiFetch(`${API}/sandboxes/${sandboxName}/policy/network-rules`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ key, rule: { name: name || key, endpoints: eps, binaries } }),
                });
                showToast(`Rule "${key}" saved.`, 'success');
                navigateTo(gwUrl(`/sandboxes/${sandboxName}/rules/${key}`));
            } catch (e) {
                showToast(`Failed to save: ${e.message}`, 'danger');
            }
        },

        async deleteRule() {
            const confirmed = await showConfirm(
                `Delete network rule "${this.ruleKey}"?`,
                { icon: 'trash', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Delete' }
            );
            if (!confirmed) return;
            try {
                await apiFetch(`${API}/sandboxes/${sandboxName}/policy/network-rules/${this.ruleKey}`, {
                    method: 'DELETE',
                });
                showToast(`Rule "${this.ruleKey}" deleted.`, 'success');
                navigateTo(gwUrl(`/sandboxes/${sandboxName}/policy`));
            } catch (e) {
                showToast(`Failed to delete: ${e.message}`, 'danger');
            }
        },
    };
}

document.addEventListener('alpine:init', () => {
    Alpine.data('ruleDetailPage', ruleDetailPage);
});
