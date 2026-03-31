/**
 * Shoreguard — Policy Pages (Alpine.js)
 * Policy overview, network/filesystem/process sections, presets.
 */

// ─── Helpers ────────────────────────────────────────────────────────────────

function countFilesystemPaths(fs) {
    if (!fs) return 0;
    return (fs.read_only || []).length + (fs.read_write || []).length;
}

function countProcessRows(policy) {
    let count = 0;
    if (policy.process) {
        if (policy.process.run_as_user) count++;
        if (policy.process.run_as_group) count++;
    }
    if (policy.landlock) {
        if (policy.landlock.compatibility) count++;
    }
    return count;
}


// ─── Policy Overview Page ───────────────────────────────────────────────────

function policyPage(name) {
    return {
        sandboxName: name,
        loading: true,
        error: '',
        policy: null,
        networkCount: 0,
        fsCount: 0,
        procCount: 0,

        async init() {
            await this.load();
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                const data = await apiFetch(`${API}/sandboxes/${name}/policy`);
                this.policy = data.policy;

                if (this.policy) {
                    this.networkCount = Object.keys(this.policy.network_policies || {}).length;
                    this.fsCount = countFilesystemPaths(this.policy.filesystem);
                    this.procCount = countProcessRows(this.policy);
                }
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        async showRevisions() {
            await showPolicyRevisions(name);
        },

        networkLabel() {
            return this.networkCount === 1 ? '1 rule' : this.networkCount + ' rules';
        },
        fsLabel() {
            return this.fsCount === 1 ? '1 path' : this.fsCount + ' paths';
        },
        procLabel() {
            return this.procCount === 1 ? '1 setting' : this.procCount + ' settings';
        },
    };
}


// ─── Network Policies Section ───────────────────────────────────────────────

function networkPoliciesPage(name) {
    return {
        sandboxName: name,
        loading: true,
        error: '',
        rules: [],

        async init() {
            await this.load();
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                const data = await apiFetch(`${API}/sandboxes/${name}/policy`);
                const networkRules = data.policy?.network_policies || {};
                this.rules = Object.entries(networkRules).map(([key, rule]) => ({
                    key,
                    name: rule.name || key,
                    showKey: key !== rule.name && key !== (rule.name || '').replace(/-/g, '_'),
                    endpoints: rule.endpoints || [],
                    binaries: rule.binaries || [],
                    topHosts: (rule.endpoints || []).slice(0, 2).map(ep => ep.host).join(', '),
                    moreCount: (rule.endpoints || []).length > 2 ? ` +${(rule.endpoints || []).length - 2}` : '',
                }));
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },
    };
}


// ─── Filesystem Policy Section ──────────────────────────────────────────────

function filesystemPolicyPage(name) {
    return {
        sandboxName: name,
        loading: true,
        error: '',
        rows: [],
        showAddForm: false,
        newPath: '',
        newAccess: 'ro',

        async init() {
            await this.load();
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                const data = await apiFetch(`${API}/sandboxes/${name}/policy`);
                const fs = data.policy?.filesystem;
                this.rows = [];
                if (fs) {
                    for (const path of (fs.read_only || [])) {
                        this.rows.push({ path, access: 'ro', label: 'Read Only', badge: 'text-bg-warning' });
                    }
                    for (const path of (fs.read_write || [])) {
                        this.rows.push({ path, access: 'rw', label: 'Read / Write', badge: 'text-bg-success' });
                    }
                }
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        async addPath() {
            const path = this.newPath.trim();
            if (!path) { showToast('Path is required.', 'warning'); return; }
            try {
                await apiFetch(`${API}/sandboxes/${name}/policy/filesystem`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path, access: this.newAccess }),
                });
                showToast(`Path "${path}" added.`, 'success');
                this.showAddForm = false;
                this.newPath = '';
                await this.load();
            } catch (e) {
                showToast(`Failed: ${e.message}`, 'danger');
            }
        },

        async deletePath(path) {
            const confirmed = await showConfirm(
                `Remove filesystem path "${path}"?`,
                { icon: 'trash', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Remove' }
            );
            if (!confirmed) return;
            try {
                await apiFetch(`${API}/sandboxes/${name}/policy/filesystem?path=${encodeURIComponent(path)}`, {
                    method: 'DELETE',
                });
                showToast(`Path "${path}" removed.`, 'success');
                await this.load();
            } catch (e) {
                showToast(`Failed: ${e.message}`, 'danger');
            }
        },
    };
}


// ─── Process Policy Section ─────────────────────────────────────────────────

function processPolicyPage(name) {
    return {
        sandboxName: name,
        loading: true,
        error: '',
        editing: false,
        runAsUser: '',
        runAsGroup: '',
        landlockCompat: '',

        async init() {
            await this.load();
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                const data = await apiFetch(`${API}/sandboxes/${name}/policy`);
                const policy = data.policy || {};
                this.runAsUser = policy.process?.run_as_user || '';
                this.runAsGroup = policy.process?.run_as_group || '';
                this.landlockCompat = policy.landlock?.compatibility || '';
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        async save() {
            try {
                await apiFetch(`${API}/sandboxes/${name}/policy/process`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        run_as_user: this.runAsUser.trim() || null,
                        run_as_group: this.runAsGroup.trim() || null,
                        landlock_compatibility: this.landlockCompat.trim() || null,
                    }),
                });
                showToast('Process policy updated.', 'success');
                this.editing = false;
                await this.load();
            } catch (e) {
                showToast(`Failed: ${e.message}`, 'danger');
            }
        },
    };
}


// ─── Apply Preset Page ──────────────────────────────────────────────────────

function presetsPage(sandboxName) {
    return {
        sandboxName,
        loading: true,
        error: '',
        presets: [],

        async init() {
            await this.load();
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                this.presets = await apiFetch(`${API_GLOBAL}/policies/presets`);
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        async apply(presetName) {
            const confirmed = await showConfirm(
                `Apply "${presetName}" preset to ${this.sandboxName}?`,
                { icon: 'shield-plus', iconColor: 'text-success', btnClass: 'btn-success', btnLabel: 'Apply' }
            );
            if (!confirmed) return;
            try {
                await apiFetch(`${API}/sandboxes/${this.sandboxName}/policy/presets/${presetName}`, { method: 'POST' });
                showToast(`Preset "${presetName}" applied.`, 'success');
                navigateTo(gwUrl('/sandboxes/' + this.sandboxName + '/policy'));
            } catch (e) {
                showToast(`Failed to apply preset: ${e.message}`, 'danger');
            }
        },
    };
}


// ─── Policy Revisions (modal, stays imperative) ────────────────────────────

async function showPolicyRevisions(sandboxName) {
    const existing = document.getElementById('policyRevisionsModal');
    if (existing) existing.remove();

    document.body.insertAdjacentHTML('beforeend', `
        <div class="modal fade" id="policyRevisionsModal" tabindex="-1">
            <div class="modal-dialog modal-dialog-centered modal-lg modal-dialog-scrollable">
                <div class="modal-content sg-modal-themed">
                    <div class="modal-header border-bottom">
                        <h5 class="modal-title"><i class="bi bi-clock-history me-2"></i>Policy Revisions</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body" id="policy-revisions-body">
                        ${renderSpinner('Loading revisions...')}
                    </div>
                    <div class="modal-footer border-0">
                        <button class="btn btn-outline-secondary" data-bs-dismiss="modal">Close</button>
                    </div>
                </div>
            </div>
        </div>
    `);

    const modal = new bootstrap.Modal(document.getElementById('policyRevisionsModal'));
    modal.show();
    document.getElementById('policyRevisionsModal').addEventListener('hidden.bs.modal', () => {
        document.getElementById('policyRevisionsModal')?.remove();
    });

    try {
        const revisions = await apiFetch(`${API}/sandboxes/${sandboxName}/policy/revisions`);
        const body = document.getElementById('policy-revisions-body');

        if (!revisions || revisions.length === 0) {
            body.innerHTML = renderEmptyState('clock-history', 'No policy revisions recorded.');
            return;
        }

        body.innerHTML = `
            <div class="table-responsive">
                <table class="table table-dark table-striped table-sm align-middle">
                    <thead>
                        <tr>
                            <th>Version</th>
                            <th>Timestamp</th>
                            <th>Network Rules</th>
                            <th>FS Paths</th>
                            <th>Details</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${revisions.map(rev => {
                            const ts = rev.timestamp ? new Date(rev.timestamp).toLocaleString() : '\u2014';
                            const version = rev.version || '\u2014';
                            const networkCount = rev.network_policies ? Object.keys(rev.network_policies).length : (rev.network_rule_count ?? '\u2014');
                            const fsCount = rev.filesystem ? countFilesystemPaths(rev.filesystem) : (rev.filesystem_path_count ?? '\u2014');
                            const summary = rev.summary || rev.description || '';
                            return `
                                <tr>
                                    <td><strong>v${escapeHtml(String(version))}</strong></td>
                                    <td class="text-muted small">${escapeHtml(ts)}</td>
                                    <td>${networkCount}</td>
                                    <td>${fsCount}</td>
                                    <td class="text-muted small">${summary ? escapeHtml(summary) : '\u2014'}</td>
                                </tr>`;
                        }).join('')}
                    </tbody>
                </table>
            </div>`;
    } catch (e) {
        const body = document.getElementById('policy-revisions-body');
        if (body) body.innerHTML = renderError(e.message);
    }
}


// ─── Presets List Page (global) ─────────────────────────────────────────────

function presetsList() {
    return {
        loading: true,
        error: '',
        presets: [],

        async init() {
            await this.load();
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                this.presets = await apiFetch(`${API_GLOBAL}/policies/presets`);
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },
    };
}


// ─── Preset Detail Page ─────────────────────────────────────────────────────

function presetDetail(presetName) {
    return {
        presetName,
        loading: true,
        error: '',
        meta: {},
        ruleEntries: [],
        sandboxes: [],
        selectedSandbox: '',

        async init() {
            await this.load();
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                const data = await apiFetch(`${API_GLOBAL}/policies/presets/${presetName}`);
                this.meta = data.preset || {};
                const rules = data.network_policies || {};
                this.ruleEntries = Object.entries(rules).map(([key, rule]) => ({
                    key,
                    name: rule.name || key,
                    endpoints: rule.endpoints || [],
                }));

                // Load sandboxes for the dropdown
                await this._loadSandboxes();
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        async _loadSandboxes() {
            if (!GW) return;
            try {
                const all = await apiFetch(`${API}/sandboxes`);
                this.sandboxes = all.filter(sb => sb.phase === 'ready');
            } catch {
                this.sandboxes = [];
            }
        },

        async applyToSandbox() {
            if (!this.selectedSandbox) return;
            const confirmed = await showConfirm(
                `Apply "${this.presetName}" preset to ${this.selectedSandbox}?`,
                { icon: 'shield-plus', iconColor: 'text-success', btnClass: 'btn-success', btnLabel: 'Apply' }
            );
            if (!confirmed) return;
            try {
                await apiFetch(`${API}/sandboxes/${this.selectedSandbox}/policy/presets/${this.presetName}`, { method: 'POST' });
                showToast(`Preset "${this.presetName}" applied.`, 'success');
                navigateTo(gwUrl('/sandboxes/' + this.selectedSandbox + '/policy'));
            } catch (e) {
                showToast(`Failed to apply preset: ${e.message}`, 'danger');
            }
        },
    };
}
