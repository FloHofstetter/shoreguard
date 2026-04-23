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
        // Policy pin state
        pin: null,
        pinning: false,
        pinReason: '',
        pinExpiresAt: '',
        // Active policy version reported by the supervisor (M18 drift check)
        activeVersion: null,

        get isPinned() { return this.pin !== null; },
        get pinnedVersion() {
            return this.pin && this.pin.pinned_version != null
                ? Number(this.pin.pinned_version)
                : null;
        },
        get hasPinDrift() {
            return this.isPinned
                && this.pinnedVersion !== null
                && this.activeVersion !== null
                && this.pinnedVersion !== this.activeVersion;
        },

        async init() {
            await this.load();
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                const [policyData, pinData, sbData] = await Promise.allSettled([
                    apiFetch(`${API}/sandboxes/${name}/policy`),
                    apiFetch(`${API}/sandboxes/${name}/policy/pin`),
                    apiFetch(`${API}/sandboxes/${name}`),
                ]);

                if (policyData.status === 'fulfilled') {
                    this.policy = policyData.value.policy;
                    if (this.policy) {
                        this.networkCount = Object.keys(this.policy.network_policies || {}).length;
                        this.fsCount = countFilesystemPaths(this.policy.filesystem);
                        this.procCount = countProcessRows(this.policy);
                    }
                } else {
                    this.error = policyData.reason?.message || 'Failed to load policy';
                }

                // Pin: 404 = not pinned (expected), anything else is an error
                this.pin = pinData.status === 'fulfilled' ? pinData.value : null;

                // current_policy_version is the supervisor-reported active
                // revision; surfaced on GET /sandboxes/{name} since M32/WS5.
                if (sbData.status === 'fulfilled' && sbData.value) {
                    const v = sbData.value.current_policy_version;
                    this.activeVersion = v == null ? null : Number(v);
                } else {
                    this.activeVersion = null;
                }
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        async pinPolicy() {
            this.pinning = true;
            try {
                const body = {};
                if (this.pinReason.trim()) body.reason = this.pinReason.trim();
                if (this.pinExpiresAt.trim()) body.expires_at = new Date(this.pinExpiresAt).toISOString();

                this.pin = await apiFetch(`${API}/sandboxes/${name}/policy/pin`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                showToast('Policy pinned.', 'success');
                this.pinReason = '';
                this.pinExpiresAt = '';
                // Close the modal
                const modal = bootstrap.Modal.getInstance(document.getElementById('pinModal'));
                if (modal) modal.hide();
            } catch (e) {
                showToast(`Failed to pin: ${e.message}`, 'danger');
            } finally {
                this.pinning = false;
            }
        },

        async unpinPolicy() {
            const confirmed = await showConfirm(
                'Remove the policy pin? This will allow policy modifications again.',
                { icon: 'unlock', iconColor: 'text-warning', btnClass: 'btn-warning', btnLabel: 'Unpin' }
            );
            if (!confirmed) return;
            try {
                await apiFetch(`${API}/sandboxes/${name}/policy/pin`, { method: 'DELETE' });
                this.pin = null;
                showToast('Policy unpinned.', 'success');
            } catch (e) {
                showToast(`Failed to unpin: ${e.message}`, 'danger');
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
        pin: null,
        get isPinned() { return this.pin !== null; },

        async init() {
            await this.load();
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                const [data, pinData] = await Promise.allSettled([
                    apiFetch(`${API}/sandboxes/${name}/policy`),
                    apiFetch(`${API}/sandboxes/${name}/policy/pin`),
                ]);
                if (data.status === 'fulfilled') {
                    const networkRules = data.value.policy?.network_policies || {};
                    this.rules = Object.entries(networkRules).map(([key, rule]) => ({
                        key,
                        name: rule.name || key,
                        showKey: key !== rule.name && key !== (rule.name || '').replace(/-/g, '_'),
                        endpoints: rule.endpoints || [],
                        binaries: rule.binaries || [],
                        topHosts: (rule.endpoints || []).slice(0, 2).map(ep => ep.host).join(', '),
                        moreCount: (rule.endpoints || []).length > 2 ? ` +${(rule.endpoints || []).length - 2}` : '',
                    }));
                } else {
                    this.error = data.reason?.message || 'Failed to load policy';
                }
                this.pin = pinData.status === 'fulfilled' ? pinData.value : null;
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
        pin: null,
        get isPinned() { return this.pin !== null; },

        async init() {
            await this.load();
        },

        openAddForm() {
            if (this.isPinned) { showToast('Policy is pinned. Unpin to edit.', 'warning'); return; }
            this.showAddForm = true;
            this.$nextTick(() => this.$refs.newPathInput?.focus());
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                const [policyRes, pinRes] = await Promise.allSettled([
                    apiFetch(`${API}/sandboxes/${name}/policy`),
                    apiFetch(`${API}/sandboxes/${name}/policy/pin`),
                ]);
                if (policyRes.status === 'fulfilled') {
                    const fs = policyRes.value.policy?.filesystem;
                    this.rows = [];
                    if (fs) {
                        for (const path of (fs.read_only || [])) {
                            this.rows.push({ path, access: 'ro', label: 'Read Only', badge: 'text-bg-warning' });
                        }
                        for (const path of (fs.read_write || [])) {
                            this.rows.push({ path, access: 'rw', label: 'Read / Write', badge: 'text-bg-success' });
                        }
                    }
                } else {
                    this.error = policyRes.reason?.message || 'Failed to load policy';
                }
                this.pin = pinRes.status === 'fulfilled' ? pinRes.value : null;
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
        pin: null,
        get isPinned() { return this.pin !== null; },

        async init() {
            await this.load();
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                const [policyRes, pinRes] = await Promise.allSettled([
                    apiFetch(`${API}/sandboxes/${name}/policy`),
                    apiFetch(`${API}/sandboxes/${name}/policy/pin`),
                ]);
                if (policyRes.status === 'fulfilled') {
                    const policy = policyRes.value.policy || {};
                    this.runAsUser = policy.process?.run_as_user || '';
                    this.runAsGroup = policy.process?.run_as_group || '';
                    this.landlockCompat = policy.landlock?.compatibility || '';
                } else {
                    this.error = policyRes.reason?.message || 'Failed to load policy';
                }
                this.pin = pinRes.status === 'fulfilled' ? pinRes.value : null;
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
        pin: null,
        get isPinned() { return this.pin !== null; },

        async init() {
            await this.load();
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                const [presetsRes, pinRes] = await Promise.allSettled([
                    apiFetch(`${API_GLOBAL}/policies/presets`),
                    apiFetch(`${API}/sandboxes/${sandboxName}/policy/pin`),
                ]);
                this.presets = presetsRes.status === 'fulfilled' ? presetsRes.value : [];
                this.pin = pinRes.status === 'fulfilled' ? pinRes.value : null;
                if (presetsRes.status === 'rejected') {
                    this.error = presetsRes.reason?.message || 'Failed to load presets';
                }
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        async apply(presetName) {
            if (this.isPinned) { showToast('Policy is pinned. Unpin to apply presets.', 'warning'); return; }
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
                        <button id="policy-diff-btn" class="btn btn-outline-info d-none" disabled>
                            <i class="bi bi-arrow-left-right me-1"></i>Compare
                        </button>
                        <button id="policy-diff-back" class="btn btn-outline-secondary d-none">
                            <i class="bi bi-arrow-left me-1"></i>Back
                        </button>
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

    let selectedA = null;
    let selectedB = null;

    function updateCompareBtn() {
        const btn = document.getElementById('policy-diff-btn');
        if (btn) {
            const enabled = selectedA != null && selectedB != null && selectedA !== selectedB;
            btn.disabled = !enabled;
            btn.classList.remove('d-none');
        }
    }

    function renderRevisionsList(revisions) {
        const body = document.getElementById('policy-revisions-body');
        const backBtn = document.getElementById('policy-diff-back');
        if (backBtn) backBtn.classList.add('d-none');
        document.getElementById('policy-diff-btn').classList.remove('d-none');

        body.innerHTML = `
            <p class="text-muted small mb-2">Select two versions to compare:</p>
            <div class="table-responsive">
                <table class="table table-striped table-sm align-middle">
                    <thead>
                        <tr>
                            <th class="sg-w-40">A</th>
                            <th class="sg-w-40">B</th>
                            <th>Version</th>
                            <th>Status</th>
                            <th>Hash</th>
                            <th>Created</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${revisions.map(rev => {
                            const version = rev.version || '\u2014';
                            const rstatus = rev.status || '\u2014';
                            const hash = rev.policy_hash ? rev.policy_hash.substring(0, 8) : '\u2014';
                            const ts = rev.created_at_ms ? new Date(rev.created_at_ms).toLocaleString() : '\u2014';
                            return `
                                <tr>
                                    <td><input type="radio" name="diff-a" value="${version}" class="form-check-input"></td>
                                    <td><input type="radio" name="diff-b" value="${version}" class="form-check-input"></td>
                                    <td><strong>v${escapeHtml(String(version))}</strong></td>
                                    <td><span class="badge text-bg-secondary">${escapeHtml(rstatus)}</span></td>
                                    <td class="text-muted small font-monospace">${escapeHtml(hash)}</td>
                                    <td class="text-muted small">${escapeHtml(ts)}</td>
                                </tr>`;
                        }).join('')}
                    </tbody>
                </table>
            </div>`;

        // Pre-select newest two if available
        if (revisions.length >= 2) {
            const radiosA = body.querySelectorAll('input[name="diff-a"]');
            const radiosB = body.querySelectorAll('input[name="diff-b"]');
            radiosB[0].checked = true;
            radiosA[1].checked = true;
            selectedB = revisions[0].version;
            selectedA = revisions[1].version;
            updateCompareBtn();
        }

        body.querySelectorAll('input[name="diff-a"]').forEach(r => {
            r.addEventListener('change', () => { selectedA = parseInt(r.value); updateCompareBtn(); });
        });
        body.querySelectorAll('input[name="diff-b"]').forEach(r => {
            r.addEventListener('change', () => { selectedB = parseInt(r.value); updateCompareBtn(); });
        });
    }

    async function showDiff() {
        const body = document.getElementById('policy-revisions-body');
        body.innerHTML = renderSpinner('Loading policy diff...');
        document.getElementById('policy-diff-btn').classList.add('d-none');
        document.getElementById('policy-diff-back').classList.remove('d-none');

        try {
            const data = await apiFetch(
                `${API}/sandboxes/${sandboxName}/policy/diff?version_a=${selectedA}&version_b=${selectedB}`
            );
            body.innerHTML = renderPolicyDiff(data);
        } catch (e) {
            body.innerHTML = renderError(e.message);
        }
    }

    try {
        const revisions = await apiFetch(`${API}/sandboxes/${sandboxName}/policy/revisions`);
        const body = document.getElementById('policy-revisions-body');

        if (!revisions || revisions.length === 0) {
            body.innerHTML = renderEmptyState('clock-history', 'No policy revisions recorded.');
            return;
        }

        renderRevisionsList(revisions);

        document.getElementById('policy-diff-btn').addEventListener('click', showDiff);
        document.getElementById('policy-diff-back').addEventListener('click', () => {
            renderRevisionsList(revisions);
        });
    } catch (e) {
        const body = document.getElementById('policy-revisions-body');
        if (body) body.innerHTML = renderError(e.message);
    }
}


// ─── Policy Diff Renderer ──────────────────────────────────────────────────

function renderPolicyDiff(data) {
    const { version_a, version_b, policy_a, policy_b } = data;
    let html = `<h6 class="mb-3">v${version_a} &rarr; v${version_b}</h6>`;

    // Network policies diff
    const netA = (policy_a && policy_a.network_policies) || {};
    const netB = (policy_b && policy_b.network_policies) || {};
    const allNetKeys = [...new Set([...Object.keys(netA), ...Object.keys(netB)])].sort();

    html += '<div class="diff-section-header"><i class="bi bi-globe me-1"></i>Network Policies</div>';
    if (allNetKeys.length === 0) {
        html += '<p class="text-muted small">No network policies in either version.</p>';
    } else {
        for (const key of allNetKeys) {
            const inA = key in netA;
            const inB = key in netB;
            const label = (inA ? netA[key].name : netB[key].name) || key;

            if (inA && !inB) {
                html += `<div class="diff-removed p-2 mb-1 rounded">
                    <span class="diff-label diff-label-removed">Removed</span>
                    <strong class="ms-2">${escapeHtml(label)}</strong>
                    <span class="text-muted small ms-2">${(netA[key].endpoints || []).length} endpoint(s)</span>
                </div>`;
            } else if (!inA && inB) {
                html += `<div class="diff-added p-2 mb-1 rounded">
                    <span class="diff-label diff-label-added">Added</span>
                    <strong class="ms-2">${escapeHtml(label)}</strong>
                    <span class="text-muted small ms-2">${(netB[key].endpoints || []).length} endpoint(s)</span>
                </div>`;
            } else {
                const changed = JSON.stringify(netA[key]) !== JSON.stringify(netB[key]);
                if (changed) {
                    const epCountA = (netA[key].endpoints || []).length;
                    const epCountB = (netB[key].endpoints || []).length;
                    html += `<div class="diff-changed p-2 mb-1 rounded">
                        <span class="diff-label diff-label-changed">Changed</span>
                        <strong class="ms-2">${escapeHtml(label)}</strong>
                        <span class="text-muted small ms-2">${epCountA} &rarr; ${epCountB} endpoint(s)</span>
                    </div>`;
                } else {
                    html += `<div class="diff-unchanged p-2 mb-1 rounded">
                        <strong>${escapeHtml(label)}</strong>
                        <span class="text-muted small ms-2">unchanged</span>
                    </div>`;
                }
            }
        }
    }

    // Filesystem diff
    html += '<div class="diff-section-header mt-3"><i class="bi bi-folder me-1"></i>Filesystem</div>';
    const fsA = (policy_a && policy_a.filesystem) || {};
    const fsB = (policy_b && policy_b.filesystem) || {};
    const roA = new Set(fsA.read_only || []);
    const roB = new Set(fsB.read_only || []);
    const rwA = new Set(fsA.read_write || []);
    const rwB = new Set(fsB.read_write || []);

    const allPaths = [...new Set([...roA, ...roB, ...rwA, ...rwB])].sort();
    if (allPaths.length === 0) {
        html += '<p class="text-muted small">No filesystem paths in either version.</p>';
    } else {
        for (const p of allPaths) {
            const accessA = roA.has(p) ? 'ro' : (rwA.has(p) ? 'rw' : null);
            const accessB = roB.has(p) ? 'ro' : (rwB.has(p) ? 'rw' : null);

            if (accessA && !accessB) {
                html += `<div class="diff-removed p-1 mb-1 rounded small">
                    <span class="diff-label diff-label-removed">Removed</span>
                    <code class="ms-2">${escapeHtml(p)}</code> <span class="text-muted">(${accessA})</span>
                </div>`;
            } else if (!accessA && accessB) {
                html += `<div class="diff-added p-1 mb-1 rounded small">
                    <span class="diff-label diff-label-added">Added</span>
                    <code class="ms-2">${escapeHtml(p)}</code> <span class="text-muted">(${accessB})</span>
                </div>`;
            } else if (accessA !== accessB) {
                html += `<div class="diff-changed p-1 mb-1 rounded small">
                    <span class="diff-label diff-label-changed">Changed</span>
                    <code class="ms-2">${escapeHtml(p)}</code> <span class="text-muted">${accessA} &rarr; ${accessB}</span>
                </div>`;
            }
        }
        const unchangedCount = allPaths.filter(p => {
            const aA = roA.has(p) ? 'ro' : (rwA.has(p) ? 'rw' : null);
            const aB = roB.has(p) ? 'ro' : (rwB.has(p) ? 'rw' : null);
            return aA === aB && aA != null;
        }).length;
        if (unchangedCount > 0) {
            html += `<p class="diff-unchanged small mt-1">${unchangedCount} path(s) unchanged</p>`;
        }
    }

    // Process / Landlock diff
    html += '<div class="diff-section-header mt-3"><i class="bi bi-gear me-1"></i>Process & Landlock</div>';
    const procA = (policy_a && policy_a.process) || {};
    const procB = (policy_b && policy_b.process) || {};
    const llA = (policy_a && policy_a.landlock) || {};
    const llB = (policy_b && policy_b.landlock) || {};

    const settingsFields = [
        ['run_as_user', procA.run_as_user, procB.run_as_user],
        ['run_as_group', procA.run_as_group, procB.run_as_group],
        ['landlock.compatibility', llA.compatibility, llB.compatibility],
    ];

    let hasProcessChanges = false;
    for (const [field, valA, valB] of settingsFields) {
        if (valA === valB) continue;
        hasProcessChanges = true;
        if (valA && !valB) {
            html += `<div class="diff-removed p-1 mb-1 rounded small">
                <span class="diff-label diff-label-removed">Removed</span>
                <strong class="ms-2">${escapeHtml(field)}</strong>: ${escapeHtml(String(valA))}
            </div>`;
        } else if (!valA && valB) {
            html += `<div class="diff-added p-1 mb-1 rounded small">
                <span class="diff-label diff-label-added">Added</span>
                <strong class="ms-2">${escapeHtml(field)}</strong>: ${escapeHtml(String(valB))}
            </div>`;
        } else {
            html += `<div class="diff-changed p-1 mb-1 rounded small">
                <span class="diff-label diff-label-changed">Changed</span>
                <strong class="ms-2">${escapeHtml(field)}</strong>: ${escapeHtml(String(valA))} &rarr; ${escapeHtml(String(valB))}
            </div>`;
        }
    }
    if (!hasProcessChanges) {
        html += '<p class="text-muted small">No process/landlock changes.</p>';
    }

    return html;
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
                const resp = await apiFetch(`${API}/sandboxes`);
                const all = Array.isArray(resp) ? resp : (resp.items || []);
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


// ─── GitOps YAML Apply (WS33.3) ────────────────────────────────────────────

function sandboxPolicyApplyYaml() {
    return {
        expanded: false,
        yamlText: '',
        applyMode: 'replace',
        expectedVersion: '',
        applying: false,
        dryRunMode: false,
        lastResult: null,

        _buildBody(dryRun) {
            const body = {
                yaml: this.yamlText,
                dry_run: dryRun,
                mode: this.applyMode,
            };
            if (this.expectedVersion.trim()) {
                body.expected_version = this.expectedVersion.trim();
            }
            return body;
        },

        _sandboxNameFromRoot() {
            // The outer policyPage component owns sandboxName; pull it via
            // $root so this section does not need its own copy.
            const root = this.$root;
            return (root && root.sandboxName) || '';
        },

        async _submit(dryRun) {
            const sb = this._sandboxNameFromRoot();
            if (!sb) {
                showToast('Could not determine sandbox name.', 'danger');
                return;
            }
            this.applying = true;
            this.dryRunMode = dryRun;
            this.lastResult = null;
            try {
                const result = await apiFetch(`${API}/sandboxes/${sb}/policy/apply`, {
                    method: 'POST',
                    body: JSON.stringify(this._buildBody(dryRun)),
                });
                this.lastResult = {
                    ok: true,
                    status: result.status || (dryRun ? 'dry_run' : 'applied'),
                    message: dryRun
                        ? 'Dry-run complete. Review the diff below; no changes written.'
                        : 'Policy applied successfully.',
                    diff: result.diff || null,
                };
                if (!dryRun) {
                    showToast(`Policy ${this.applyMode}d.`, 'success');
                }
            } catch (e) {
                // Special-case: HTTP 400 merge_unsupported → guide back to replace.
                const msg = e?.message || String(e);
                if (msg.includes('merge_unsupported')) {
                    this.lastResult = {
                        ok: false,
                        status: 'Merge mode not applicable',
                        message: 'This change touches filesystem, process, or landlock. Retry with Apply mode = Replace.',
                        diff: null,
                    };
                    showToast('Merge mode cannot express this diff — use Replace.', 'warning');
                } else {
                    this.lastResult = {
                        ok: false,
                        status: 'Apply failed',
                        message: msg,
                        diff: null,
                    };
                    showToast(`Apply failed: ${msg}`, 'danger');
                }
            } finally {
                this.applying = false;
            }
        },

        runDryRun() { return this._submit(true); },
        runApply() { return this._submit(false); },
    };
}


// ─── Alpine.data registrations ─────────────────────────────────────────────

document.addEventListener('alpine:init', () => {
    Alpine.data('policyPage', policyPage);
    Alpine.data('networkPoliciesPage', networkPoliciesPage);
    Alpine.data('filesystemPolicyPage', filesystemPolicyPage);
    Alpine.data('processPolicyPage', processPolicyPage);
    Alpine.data('presetsPage', presetsPage);
    Alpine.data('presetDetail', presetDetail);
    Alpine.data('sandboxPolicyApplyYaml', sandboxPolicyApplyYaml);
    // Spread-merge factory replacing inline `{ ...presetsList(), ...sortableTable('name') }`.
    Alpine.data('presetsListPage', () => ({ ...presetsList(), ...sortableTable('name') }));
});
