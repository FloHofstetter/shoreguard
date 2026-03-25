/**
 * Shoreguard — Approvals Tab
 * Table-based draft policy approval flow.
 */

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
                    <button class="btn btn-success" onclick="approveAllChunks('${name}')">
                        <i class="bi bi-check-all me-1"></i>Approve All
                    </button>
                    <button class="btn btn-outline-secondary" onclick="clearChunks('${name}')">
                        Clear All
                    </button>
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
    if (chunk.status === 'pending') {
        actions = `
            <div class="btn-group btn-group-sm">
                <button class="btn btn-success" onclick="event.stopPropagation(); approveChunk('${sandboxName}', '${chunk.id}')" title="Approve">
                    <i class="bi bi-check"></i>
                </button>
                <button class="btn btn-outline-danger" onclick="event.stopPropagation(); rejectChunk('${sandboxName}', '${chunk.id}')" title="Reject">
                    <i class="bi bi-x"></i>
                </button>
            </div>`;
    } else if (chunk.status === 'approved') {
        actions = `
            <button class="btn btn-outline-secondary btn-sm" onclick="event.stopPropagation(); undoChunk('${sandboxName}', '${chunk.id}')" title="Undo">
                <i class="bi bi-arrow-counterclockwise"></i>
            </button>`;
    }

    const confidenceHtml = chunk.confidence > 0
        ? `<div class="progress confidence-bar d-inline-flex" title="${Math.round(chunk.confidence * 100)}%">
               <div class="progress-bar bg-info" style="width:${Math.round(chunk.confidence * 100)}%"></div>
           </div>`
        : '<span class="text-muted">—</span>';

    return `
        <tr ${hasDetail ? `class="table-clickable" onclick="toggleChunkDetail('${chunk.id}')"` : ''}>
            <td><span class="badge ${badgeClass}">${chunk.status}</span></td>
            <td>
                <strong>${escapeHtml(chunk.rule_name)}</strong>
                ${hasDetail ? '<i class="bi bi-chevron-right expand-chevron ms-1 small" id="chunk-chevron-' + chunk.id + '"></i>' : ''}
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
    } catch (e) {
        showToast(`Approve failed: ${e.message}`, 'danger');
    }
    navigateTo('/sandboxes/' + name);
}

async function rejectChunk(name, chunkId) {
    try {
        await apiFetch(`${API}/sandboxes/${name}/approvals/${chunkId}/reject`, { method: 'POST' });
        showToast('Chunk rejected.', 'warning');
    } catch (e) {
        showToast(`Reject failed: ${e.message}`, 'danger');
    }
    navigateTo('/sandboxes/' + name);
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
    } catch (e) {
        showToast(`Approve all failed: ${e.message}`, 'danger');
    }
    navigateTo('/sandboxes/' + name);
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
    } catch (e) {
        showToast(`Clear failed: ${e.message}`, 'danger');
    }
    navigateTo('/sandboxes/' + name);
}

async function undoChunk(name, chunkId) {
    try {
        await apiFetch(`${API}/sandboxes/${name}/approvals/${chunkId}/undo`, { method: 'POST' });
        showToast('Approval undone.', 'warning');
    } catch (e) {
        showToast(`Undo failed: ${e.message}`, 'danger');
    }
    navigateTo('/sandboxes/' + name);
}
