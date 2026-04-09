/**
 * Shoreguard — Approvals Page (Alpine.js)
 * Table-based draft policy approval flow.
 */

function approvalsPage(name) {
    return {
        sandboxName: name,
        loading: true,
        error: '',
        chunks: [],
        rollingSummary: '',
        expandedChunks: {},

        get pendingCount() {
            return this.chunks.filter(c => c.status === 'pending').length;
        },

        async init() {
            await this.load();
            // Connect WebSocket for live draft_policy_update events
            if (typeof connectWebSocket === 'function') {
                // The WebSocket handler in websocket.js will auto-refresh
                // approvals-content if it exists, but we override that
                // by listening for the custom event pattern
            }
        },

        async load() {
            this.loading = true;
            this.error = '';
            try {
                const data = await apiFetch(`${API}/sandboxes/${name}/approvals`);
                this.chunks = data.chunks || [];
                this.rollingSummary = data.rolling_summary || '';
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        toggleDetail(chunkId) {
            this.expandedChunks[chunkId] = !this.expandedChunks[chunkId];
        },

        isExpanded(chunkId) {
            return !!this.expandedChunks[chunkId];
        },

        hasDetail(chunk) {
            return !!(chunk.rationale || chunk.security_notes);
        },

        confidencePercent(chunk) {
            return Math.round((chunk.confidence || 0) * 100);
        },

        endpointBadges(endpoints, max = 2) {
            if (!endpoints || endpoints.length === 0) return [];
            return endpoints.slice(0, max).map(ep => `${ep.host}:${ep.port}`);
        },

        endpointMore(endpoints, max = 2) {
            if (!endpoints || endpoints.length <= max) return 0;
            return endpoints.length - max;
        },

        async approve(chunkId) {
            try {
                await apiFetch(`${API}/sandboxes/${name}/approvals/${chunkId}/approve`, { method: 'POST' });
                showToast('Chunk approved.', 'success');
                await this.load();
            } catch (e) {
                showToast(`Approve failed: ${e.message}`, 'danger');
            }
        },

        async reject(chunkId) {
            try {
                await apiFetch(`${API}/sandboxes/${name}/approvals/${chunkId}/reject`, { method: 'POST' });
                showToast('Chunk rejected.', 'warning');
                await this.load();
            } catch (e) {
                showToast(`Reject failed: ${e.message}`, 'danger');
            }
        },

        async undo(chunkId) {
            try {
                await apiFetch(`${API}/sandboxes/${name}/approvals/${chunkId}/undo`, { method: 'POST' });
                showToast('Approval undone.', 'warning');
                await this.load();
            } catch (e) {
                showToast(`Undo failed: ${e.message}`, 'danger');
            }
        },

        async approveAll() {
            const confirmed = await showConfirm(
                'Approve all pending recommendations?',
                { icon: 'check-all', iconColor: 'text-success', btnClass: 'btn-success', btnLabel: 'Approve All' }
            );
            if (!confirmed) return;
            try {
                await apiFetch(`${API}/sandboxes/${name}/approvals/approve-all`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ include_security_flagged: false }),
                });
                showToast('All pending chunks approved.', 'success');
                await this.load();
            } catch (e) {
                showToast(`Approve all failed: ${e.message}`, 'danger');
            }
        },

        async clearAll() {
            const confirmed = await showConfirm(
                'Clear all pending recommendations?',
                { icon: 'trash', iconColor: 'text-warning', btnClass: 'btn-warning', btnLabel: 'Clear All' }
            );
            if (!confirmed) return;
            try {
                await apiFetch(`${API}/sandboxes/${name}/approvals/clear`, { method: 'POST' });
                showToast('All chunks cleared.', 'success');
                await this.load();
            } catch (e) {
                showToast(`Clear failed: ${e.message}`, 'danger');
            }
        },

        openEdit(chunkId) {
            const chunk = this.chunks.find(c => c.id === chunkId);
            if (!chunk) return;
            openEditChunkModal(name, chunk, () => this.load());
        },

        async showHistory() {
            await showApprovalHistory(name);
        },
    };
}


// ─── Edit Chunk Modal (imperative, Bootstrap modal) ─────────────────────────

function openEditChunkModal(sandboxName, chunk, onSave) {
    const rule = chunk.proposed_rule || {};
    const json = JSON.stringify(rule, null, 2);

    const existing = document.getElementById('editChunkModal');
    if (existing) existing.remove();

    document.body.insertAdjacentHTML('beforeend', `
        <div class="modal fade" id="editChunkModal" tabindex="-1">
            <div class="modal-dialog modal-dialog-centered modal-lg">
                <div class="modal-content sg-modal-themed">
                    <div class="modal-header border-bottom">
                        <h5 class="modal-title"><i class="bi bi-pencil me-2"></i>Edit Proposed Rule</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <p class="text-muted small mb-2">Edit the proposed rule JSON. Changes are saved as a new proposal.</p>
                        <textarea id="edit-chunk-json" class="form-control font-monospace" rows="14" spellcheck="false">${escapeHtml(json)}</textarea>
                        <div id="edit-chunk-output" class="mt-2"></div>
                    </div>
                    <div class="modal-footer border-0">
                        <button class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button>
                        <button class="btn btn-success" id="edit-chunk-save-btn">
                            <i class="bi bi-check me-1"></i>Save
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `);

    const modal = new bootstrap.Modal(document.getElementById('editChunkModal'));
    modal.show();

    document.getElementById('editChunkModal').addEventListener('hidden.bs.modal', () => {
        document.getElementById('editChunkModal')?.remove();
    });

    document.getElementById('edit-chunk-save-btn').addEventListener('click', async () => {
        const textarea = document.getElementById('edit-chunk-json');
        const output = document.getElementById('edit-chunk-output');
        const btn = document.getElementById('edit-chunk-save-btn');

        let proposed_rule;
        try {
            proposed_rule = JSON.parse(textarea.value);
        } catch (e) {
            output.innerHTML = `<div class="text-danger small"><i class="bi bi-x-circle me-1"></i>Invalid JSON: ${escapeHtml(e.message)}</div>`;
            return;
        }

        btn.disabled = true;
        output.innerHTML = '<div class="text-muted small"><div class="spinner-border spinner-border-sm me-2"></div>Saving...</div>';

        try {
            await apiFetch(`${API}/sandboxes/${sandboxName}/approvals/${chunk.id}/edit`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ proposed_rule }),
            });
            showToast('Proposed rule updated.', 'success');
            bootstrap.Modal.getInstance(document.getElementById('editChunkModal'))?.hide();
            if (onSave) onSave();
        } catch (e) {
            output.innerHTML = `<div class="text-danger small"><i class="bi bi-x-circle me-1"></i>${escapeHtml(e.message)}</div>`;
        } finally {
            btn.disabled = false;
        }
    });
}


// ─── Approval History Modal (imperative) ────────────────────────────────────

async function showApprovalHistory(sandboxName) {
    const existing = document.getElementById('approvalHistoryModal');
    if (existing) existing.remove();

    document.body.insertAdjacentHTML('beforeend', `
        <div class="modal fade" id="approvalHistoryModal" tabindex="-1">
            <div class="modal-dialog modal-dialog-centered modal-lg modal-dialog-scrollable">
                <div class="modal-content sg-modal-themed">
                    <div class="modal-header border-bottom">
                        <h5 class="modal-title"><i class="bi bi-clock-history me-2"></i>Approval History</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body" id="approval-history-body">
                        ${renderSpinner('Loading history...')}
                    </div>
                    <div class="modal-footer border-0">
                        <button class="btn btn-outline-secondary" data-bs-dismiss="modal">Close</button>
                    </div>
                </div>
            </div>
        </div>
    `);

    const modal = new bootstrap.Modal(document.getElementById('approvalHistoryModal'));
    modal.show();
    document.getElementById('approvalHistoryModal').addEventListener('hidden.bs.modal', () => {
        document.getElementById('approvalHistoryModal')?.remove();
    });

    try {
        const history = await apiFetch(`${API}/sandboxes/${sandboxName}/approvals/history`);
        const body = document.getElementById('approval-history-body');

        if (!history || history.length === 0) {
            body.innerHTML = renderEmptyState('clock-history', 'No approval history yet.');
            return;
        }

        body.innerHTML = `
            <div class="table-responsive">
                <table class="table table-striped table-sm align-middle">
                    <thead>
                        <tr>
                            <th>Rule</th>
                            <th>Decision</th>
                            <th>Timestamp</th>
                            <th>Details</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${history.map(entry => {
                            const badgeClass = SG.badges.approval[entry.status] || SG.badges.approval[entry.decision] || 'text-bg-secondary';
                            const decision = entry.status || entry.decision || 'unknown';
                            const ts = entry.timestamp ? new Date(entry.timestamp).toLocaleString() : '\u2014';
                            const rule = entry.rule_name || entry.chunk_id || '\u2014';
                            const reason = entry.reason || '';
                            return `
                                <tr>
                                    <td><strong>${escapeHtml(rule)}</strong></td>
                                    <td><span class="badge ${badgeClass}">${escapeHtml(decision)}</span></td>
                                    <td class="text-muted small">${escapeHtml(ts)}</td>
                                    <td class="text-muted small">${reason ? escapeHtml(reason) : '\u2014'}</td>
                                </tr>`;
                        }).join('')}
                    </tbody>
                </table>
            </div>`;
    } catch (e) {
        const body = document.getElementById('approval-history-body');
        if (body) body.innerHTML = renderError(e.message);
    }
}

document.addEventListener('alpine:init', () => {
    Alpine.data('approvalsPage', approvalsPage);
});
