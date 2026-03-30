/**
 * Shoreguard — Approvals Tab
 * Table-based draft policy approval flow.
 */

let _chunksCache = [];

async function loadApprovalsPage(name) {
    const container = document.getElementById('approvals-content');
    return _loadApprovals(name, container);
}

async function loadApprovalsTab(name, container) {
    return _loadApprovals(name, container);
}

async function _loadApprovals(name, container) {
    container.innerHTML = renderSpinner('Loading approvals...');
    try {
        const data = await apiFetch(`${API}/sandboxes/${name}/approvals`);
        const chunks = data.chunks || [];
        _chunksCache = chunks;

        if (chunks.length === 0) {
            container.innerHTML = `
                <div class="text-center text-muted py-4">
                    <i class="bi bi-shield-check fs-1 d-block mb-2"></i>
                    <p>No draft policy recommendations.</p>
                </div>`;
            return;
        }

        const pendingCount = chunks.filter(c => c.status === 'pending').length;

        container.innerHTML = `
            <div class="d-flex justify-content-between align-items-center mb-3">
                <div>
                    <span class="text-muted">${pendingCount} pending of ${chunks.length} total</span>
                </div>
                <div class="btn-group btn-group-sm">
                    <button class="btn btn-outline-secondary" onclick="showApprovalHistory('${escapeHtml(name)}')" title="History">
                        <i class="bi bi-clock-history me-1"></i>History
                    </button>
                    ${_sgHasRole('operator') ? `
                    <button class="btn btn-success" onclick="approveAllChunks('${escapeHtml(name)}')">
                        <i class="bi bi-check-all me-1"></i>Approve All
                    </button>
                    <button class="btn btn-outline-secondary" onclick="clearChunks('${escapeHtml(name)}')">
                        Clear All
                    </button>` : ''}
                </div>
            </div>

            ${data.rolling_summary ? `<div class="alert alert-info small py-2 mb-3"><i class="bi bi-lightbulb me-1"></i>${escapeHtml(data.rolling_summary)}</div>` : ''}

            <div class="table-responsive">
                <table class="table table-dark table-striped table-hover table-sm align-middle">
                    <thead>
                        <tr>
                            <th>Status</th>
                            <th>Rule</th>
                            <th>Binary</th>
                            <th>Endpoints</th>
                            <th class="text-end">Hits</th>
                            <th class="text-center">Confidence</th>
                            <th class="text-end">Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${chunks.map(chunk => renderApprovalRow(name, chunk)).join('')}
                    </tbody>
                </table>
            </div>
        `;
    } catch (e) {
        container.innerHTML = `<div class="alert alert-danger">${escapeHtml(e.message)}</div>`;
    }
}

function renderApprovalRow(sandboxName, chunk) {
    const badgeClass = SG.badges.approval[chunk.status] || 'text-bg-secondary';
    const rule = chunk.proposed_rule || {};
    const endpoints = rule.endpoints || [];

    const hasDetail = chunk.rationale || chunk.security_notes;
    const detailId = `chunk-detail-${chunk.id}`;

    let actions = '';
    if (chunk.status === 'pending' && _sgHasRole('operator')) {
        actions = `
            <div class="btn-group btn-group-sm">
                <button class="btn btn-outline-secondary" onclick="event.stopPropagation(); openEditChunk('${escapeHtml(sandboxName)}', '${escapeHtml(chunk.id)}')" title="Edit">
                    <i class="bi bi-pencil"></i>
                </button>
                <button class="btn btn-success" onclick="event.stopPropagation(); approveChunk('${escapeHtml(sandboxName)}', '${escapeHtml(chunk.id)}')" title="Approve">
                    <i class="bi bi-check"></i>
                </button>
                <button class="btn btn-outline-danger" onclick="event.stopPropagation(); rejectChunk('${escapeHtml(sandboxName)}', '${escapeHtml(chunk.id)}')" title="Reject">
                    <i class="bi bi-x"></i>
                </button>
            </div>`;
    } else if (chunk.status === 'approved' && _sgHasRole('operator')) {
        actions = `
            <button class="btn btn-outline-secondary btn-sm" onclick="event.stopPropagation(); undoChunk('${escapeHtml(sandboxName)}', '${escapeHtml(chunk.id)}')" title="Undo">
                <i class="bi bi-arrow-counterclockwise"></i>
            </button>`;
    }

    const confidenceHtml = chunk.confidence > 0
        ? `<div class="progress confidence-bar d-inline-flex" title="${Math.round(chunk.confidence * 100)}%">
               <div class="progress-bar bg-info" style="width:${Math.round(chunk.confidence * 100)}%"></div>
           </div>`
        : '<span class="text-muted">—</span>';

    return `
        <tr ${hasDetail ? `class="table-clickable" onclick="toggleChunkDetail('${escapeHtml(chunk.id)}')"` : ''}>
            <td><span class="badge ${badgeClass}">${chunk.status}</span></td>
            <td>
                <strong>${escapeHtml(chunk.rule_name)}</strong>
                ${hasDetail ? '<i class="bi bi-chevron-right expand-chevron ms-1 small" id="chunk-chevron-' + escapeHtml(chunk.id) + '"></i>' : ''}
            </td>
            <td class="font-monospace small text-muted">${chunk.binary ? escapeHtml(chunk.binary) : '—'}</td>
            <td>${renderEndpointBadges(endpoints, 2)}</td>
            <td class="text-end">${chunk.hit_count > 1 ? chunk.hit_count : '—'}</td>
            <td class="text-center">${confidenceHtml}</td>
            <td class="text-end">${actions}</td>
        </tr>
        ${hasDetail ? `
            <tr class="detail-row" id="${detailId}" style="display:none">
                <td></td>
                <td colspan="6">
                    ${chunk.rationale ? `<p class="small text-muted mb-1"><i class="bi bi-chat-quote me-1"></i>${escapeHtml(chunk.rationale)}</p>` : ''}
                    ${chunk.security_notes ? `<div class="alert alert-warning small py-1 px-2 mb-0"><i class="bi bi-exclamation-triangle me-1"></i>${escapeHtml(chunk.security_notes)}</div>` : ''}
                </td>
            </tr>
        ` : ''}`;
}

function toggleChunkDetail(chunkId) {
    const row = document.getElementById(`chunk-detail-${chunkId}`);
    const chevron = document.getElementById(`chunk-chevron-${chunkId}`);
    if (!row) return;
    const isOpen = row.style.display !== 'none';
    row.style.display = isOpen ? 'none' : '';
    chevron?.classList.toggle('open', !isOpen);
}

function showApprovals(sandboxName) {
    // Click the approvals tab in the context bar
    const approvalsTab = document.querySelector('#ctx-tabs .nav-link[data-tab="approvals"]');
    if (approvalsTab) approvalsTab.click();
}

async function approveChunk(name, chunkId) {
    try {
        await apiFetch(`${API}/sandboxes/${name}/approvals/${chunkId}/approve`, { method: 'POST' });
        showToast('Chunk approved.', 'success');
        navigateTo(gwUrl('/sandboxes/' + name));
    } catch (e) {
        showToast(`Approve failed: ${e.message}`, 'danger');
    }
}

async function rejectChunk(name, chunkId) {
    try {
        await apiFetch(`${API}/sandboxes/${name}/approvals/${chunkId}/reject`, { method: 'POST' });
        showToast('Chunk rejected.', 'warning');
        navigateTo(gwUrl('/sandboxes/' + name));
    } catch (e) {
        showToast(`Reject failed: ${e.message}`, 'danger');
    }
}

async function approveAllChunks(name) {
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
        navigateTo(gwUrl('/sandboxes/' + name));
    } catch (e) {
        showToast(`Approve all failed: ${e.message}`, 'danger');
    }
}

async function clearChunks(name) {
    const confirmed = await showConfirm(
        'Clear all pending recommendations?',
        { icon: 'trash', iconColor: 'text-warning', btnClass: 'btn-warning', btnLabel: 'Clear All' }
    );
    if (!confirmed) return;
    try {
        await apiFetch(`${API}/sandboxes/${name}/approvals/clear`, { method: 'POST' });
        showToast('All chunks cleared.', 'success');
        navigateTo(gwUrl('/sandboxes/' + name));
    } catch (e) {
        showToast(`Clear failed: ${e.message}`, 'danger');
    }
}

function openEditChunk(sandboxName, chunkId) {
    const chunk = _chunksCache.find(c => c.id === chunkId);
    if (!chunk) return;

    const rule = chunk.proposed_rule || {};
    const json = JSON.stringify(rule, null, 2);

    // Create a modal dynamically
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
                        <button class="btn btn-success" id="edit-chunk-save-btn" onclick="saveEditChunk('${escapeHtml(sandboxName)}', '${escapeHtml(chunkId)}')">
                            <i class="bi bi-check me-1"></i>Save
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `);

    new bootstrap.Modal(document.getElementById('editChunkModal')).show();
    // Cleanup on close
    document.getElementById('editChunkModal').addEventListener('hidden.bs.modal', () => {
        document.getElementById('editChunkModal')?.remove();
    });
}

async function saveEditChunk(sandboxName, chunkId) {
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
        await apiFetch(`${API}/sandboxes/${sandboxName}/approvals/${chunkId}/edit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ proposed_rule }),
        });
        showToast('Proposed rule updated.', 'success');
        bootstrap.Modal.getInstance(document.getElementById('editChunkModal'))?.hide();
        // Reload the approvals view
        const container = document.getElementById('approvals-content');
        if (container) _loadApprovals(sandboxName, container);
    } catch (e) {
        output.innerHTML = `<div class="text-danger small"><i class="bi bi-x-circle me-1"></i>${escapeHtml(e.message)}</div>`;
    } finally {
        btn.disabled = false;
    }
}

async function showApprovalHistory(sandboxName) {
    // Create modal dynamically
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
                <table class="table table-dark table-striped table-sm align-middle">
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
                            const ts = entry.timestamp ? new Date(entry.timestamp).toLocaleString() : '—';
                            const rule = entry.rule_name || entry.chunk_id || '—';
                            const reason = entry.reason || '';
                            return `
                                <tr>
                                    <td><strong>${escapeHtml(rule)}</strong></td>
                                    <td><span class="badge ${badgeClass}">${escapeHtml(decision)}</span></td>
                                    <td class="text-muted small">${escapeHtml(ts)}</td>
                                    <td class="text-muted small">${reason ? escapeHtml(reason) : '—'}</td>
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

async function undoChunk(name, chunkId) {
    try {
        await apiFetch(`${API}/sandboxes/${name}/approvals/${chunkId}/undo`, { method: 'POST' });
        showToast('Approval undone.', 'warning');
        navigateTo(gwUrl('/sandboxes/' + name));
    } catch (e) {
        showToast(`Undo failed: ${e.message}`, 'danger');
    }
}
