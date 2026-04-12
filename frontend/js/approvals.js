/**
 * Shoreguard — Approvals Page (Alpine.js)
 * Table-based draft policy approval flow.
 */

// Known draft-history event types and their visual treatment. Used by the
// history modal to colour + filter entries. Keys match the `event_type` field
// upstream emits; see openshell.proto DraftHistoryEntry.
const _HISTORY_EVENT_TYPES = [
    { type: 'denial_detected', label: 'Denial detected', badge: 'text-bg-warning' },
    { type: 'analysis_cycle', label: 'Analysis cycle', badge: 'text-bg-secondary' },
    { type: 'approved', label: 'Approved', badge: 'text-bg-success' },
    { type: 'rejected', label: 'Rejected', badge: 'text-bg-danger' },
    { type: 'edited', label: 'Edited', badge: 'text-bg-info' },
    { type: 'undone', label: 'Undone', badge: 'text-bg-info' },
    { type: 'cleared', label: 'Cleared', badge: 'text-bg-secondary' },
];

function approvalsPage(name) {
    return {
        sandboxName: name,
        loading: true,
        error: '',
        chunks: [],
        rollingSummary: '',
        lastAnalyzedAtMs: 0,
        expandedChunks: {},
        sortPersistentFirst: false,

        // After a Logs → Approvals navigation via hash fragment
        // (#binary=X&host=Y), the matching chunk is scrolled into view and
        // temporarily highlighted; non-matching chunks stay visible.
        highlightChunkId: '',

        get pendingCount() {
            return this.chunks.filter(c => c.status === 'pending').length;
        },

        get sortedChunks() {
            if (!this.sortPersistentFirst) return this.chunks;
            return [...this.chunks].sort((a, b) => {
                const ap = a.denial_context && a.denial_context.persistent ? 1 : 0;
                const bp = b.denial_context && b.denial_context.persistent ? 1 : 0;
                return bp - ap;
            });
        },

        async init() {
            await this.load();

            // Live-refresh: websocket.js dispatches this custom event when a
            // draft_policy_update arrives over the sandbox WS stream. We just
            // reload — load() is idempotent and cheap.
            document.addEventListener('sg:approvals-update', (event) => {
                if (event.detail && event.detail.sandbox_name === this.sandboxName) {
                    this.load();
                }
            });

            // Hash-fragment cross-link from the sandbox logs viewer:
            // #binary=/usr/bin/curl&host=api.example.com → find and highlight
            // the matching pending chunk.
            this.$nextTick(() => this._applyHashCrossLink());
            window.addEventListener('hashchange', () => this._applyHashCrossLink());
        },

        async load() {
            // Guard against overlapping reloads triggered by the websocket
            // burst + user clicks at the same time.
            if (this.loading && this.chunks.length > 0) return;
            this.loading = true;
            this.error = '';
            try {
                const data = await apiFetch(`${API}/sandboxes/${name}/approvals`);
                this.chunks = data.chunks || [];
                this.rollingSummary = data.rolling_summary || '';
                this.lastAnalyzedAtMs = data.last_analyzed_at_ms || 0;
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
            return !!(
                chunk.rationale ||
                chunk.security_notes ||
                chunk.stage ||
                (chunk.denial_summary_ids && chunk.denial_summary_ids.length > 0) ||
                chunk.binary ||
                chunk.denial_context
            );
        },

        ancestryBreadcrumb(ctx) {
            if (!ctx || !ctx.ancestors || ctx.ancestors.length === 0) return '';
            return ctx.ancestors.join(' \u2192 ');
        },

        truncatedSha(ctx) {
            if (!ctx || !ctx.binary_sha256) return '';
            return ctx.binary_sha256.slice(0, 16) + '\u2026';
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

        // Relative "seen" summary used in the Seen column:
        // "3× / last 2h ago" when hit_count > 1, single timestamp otherwise.
        formatSeen(chunk) {
            const first = chunk.first_seen_ms || 0;
            const last = chunk.last_seen_ms || 0;
            if (!first && !last) return '\u2014';
            const lastStr = last ? formatTimestamp(last) : '\u2014';
            if (chunk.hit_count && chunk.hit_count > 1) {
                return `${chunk.hit_count}\u00d7, last ${lastStr}`;
            }
            return lastStr;
        },

        // Navigate to the sandbox logs viewer with a pre-populated text
        // filter so the OCSF events that triggered this chunk are easy to
        // spot. Uses binary (if known) + first endpoint host as filter seed.
        goToLogs(chunk) {
            const parts = [];
            if (chunk.binary) parts.push(chunk.binary);
            const endpoints = (chunk.proposed_rule && chunk.proposed_rule.endpoints) || [];
            if (endpoints.length > 0 && endpoints[0].host) parts.push(endpoints[0].host);
            const filter = parts.join(' ');
            const url = `/gateways/${GW}/sandboxes/${this.sandboxName}/logs`
                + (filter ? `?text=${encodeURIComponent(filter)}` : '');
            window.location.href = url;
        },

        _parseHashFragment() {
            const hash = (window.location.hash || '').replace(/^#/, '');
            if (!hash) return {};
            const out = {};
            for (const part of hash.split('&')) {
                const [k, v] = part.split('=', 2);
                if (k) out[decodeURIComponent(k)] = v ? decodeURIComponent(v) : '';
            }
            return out;
        },

        _applyHashCrossLink() {
            const params = this._parseHashFragment();
            if (!params.binary && !params.host) return;
            const match = this.chunks.find(c => {
                if (params.binary && c.binary !== params.binary) return false;
                if (params.host) {
                    const endpoints = (c.proposed_rule && c.proposed_rule.endpoints) || [];
                    if (!endpoints.some(ep => ep.host === params.host)) return false;
                }
                return true;
            });
            if (!match) return;
            this.highlightChunkId = match.id;
            this.expandedChunks[match.id] = true;
            this.$nextTick(() => {
                const el = document.getElementById(`chunk-row-${match.id}`);
                if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                // Clear the highlight after a short moment so it works as a
                // visual cue, not a permanent marker.
                setTimeout(() => { this.highlightChunkId = ''; }, 3500);
            });
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


// ─── Approval History Modal (imperative, with event-type filter chips) ─────

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

        // Count how many entries fall into each known event type (plus a
        // fallback bucket for anything upstream adds later).
        const counts = {};
        for (const entry of history) {
            const t = entry.event_type || 'unknown';
            counts[t] = (counts[t] || 0) + 1;
        }

        const renderChips = () => _HISTORY_EVENT_TYPES
            .filter(t => counts[t.type])
            .map(t => `
                <button type="button"
                        class="btn btn-sm me-1 mb-1 history-chip ${t.badge}"
                        data-event-type="${escapeHtml(t.type)}">
                    ${escapeHtml(t.label)}
                    <span class="badge bg-dark ms-1">${counts[t.type]}</span>
                </button>
            `).join('');

        body.innerHTML = `
            <div class="mb-3 d-flex flex-wrap align-items-center">
                <span class="text-muted small me-2">Filter:</span>
                ${renderChips()}
            </div>
            <div class="table-responsive">
                <table class="table table-striped table-sm align-middle" id="approval-history-table">
                    <thead>
                        <tr>
                            <th>Event</th>
                            <th>Timestamp</th>
                            <th>Chunk</th>
                            <th>Description</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${history.map(entry => {
                            const typeInfo = _HISTORY_EVENT_TYPES.find(t => t.type === entry.event_type);
                            const badgeClass = typeInfo ? typeInfo.badge : 'text-bg-secondary';
                            const label = typeInfo ? typeInfo.label : (entry.event_type || 'unknown');
                            const ts = entry.timestamp_ms
                                ? formatTimestamp(entry.timestamp_ms)
                                : '\u2014';
                            const chunk = entry.chunk_id || '\u2014';
                            const desc = entry.description || '';
                            return `
                                <tr data-event-type="${escapeHtml(entry.event_type || 'unknown')}">
                                    <td><span class="badge ${badgeClass}">${escapeHtml(label)}</span></td>
                                    <td class="text-muted small">${escapeHtml(ts)}</td>
                                    <td class="font-monospace small">${escapeHtml(chunk)}</td>
                                    <td class="text-muted small">${desc ? escapeHtml(desc) : '\u2014'}</td>
                                </tr>`;
                        }).join('')}
                    </tbody>
                </table>
            </div>`;

        // Chip toggles hide/show rows via data-event-type. Local state so we
        // don't wrangle Alpine inside an imperative modal.
        const hidden = new Set();
        body.querySelectorAll('.history-chip').forEach(chip => {
            chip.addEventListener('click', () => {
                const t = chip.dataset.eventType;
                if (hidden.has(t)) {
                    hidden.delete(t);
                    chip.classList.remove('opacity-50');
                } else {
                    hidden.add(t);
                    chip.classList.add('opacity-50');
                }
                body.querySelectorAll('#approval-history-table tbody tr').forEach(row => {
                    row.style.display = hidden.has(row.dataset.eventType) ? 'none' : '';
                });
            });
        });
    } catch (e) {
        const body = document.getElementById('approval-history-body');
        if (body) body.innerHTML = renderError(e.message);
    }
}

document.addEventListener('alpine:init', () => {
    Alpine.data('approvalsPage', approvalsPage);
});
