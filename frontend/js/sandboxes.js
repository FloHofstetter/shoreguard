/**
 * Shoreguard — Sandbox List & Detail View
 * Table-based sandbox list, detail view with context bar tabs, sandbox actions.
 */

// Phase badge classes are in SG.badges.phase (constants.js)

// ─── Sandbox List ────────────────────────────────────────────────────────────

async function refreshSandboxes() {
    const tbody = document.getElementById('sandbox-list');
    tbody.innerHTML = `<tr><td colspan="7">${renderSpinner('Loading sandboxes...')}</td></tr>`;
    try {
        const sandboxes = await apiFetch(`${API}/sandboxes`);

        if (sandboxes.length === 0) {
            tbody.innerHTML = `
                <tr><td colspan="7" class="text-center text-muted py-5">
                    <i class="bi bi-inbox fs-1 d-block mb-3"></i>
                    <p>No sandboxes running.</p>
                    <button class="btn btn-outline-success btn-sm" onclick="navigateTo(gwUrl('/wizard'))">
                        <i class="bi bi-plus-circle me-1"></i>Create Sandbox
                    </button>
                </td></tr>`;
            return;
        }

        tbody.innerHTML = sandboxes.map(sb => `
            <tr onclick="navigateTo(gwUrl('/sandboxes/${sb.name}'))">
                <td><strong>${escapeHtml(sb.name)}</strong></td>
                <td class="d-none d-md-table-cell"><span class="font-monospace small cell-truncate" title="${escapeHtml(sb.image || '')}">${escapeHtml(sb.image || 'Default')}</span></td>
                <td><span class="badge ${SG.badges.phase[sb.phase] || 'text-bg-secondary'}">${sb.phase}</span></td>
                <td class="d-none d-md-table-cell"><span class="badge text-bg-secondary">v${sb.current_policy_version}</span></td>
                <td class="d-none d-lg-table-cell">${sb.gpu ? '<i class="bi bi-gpu-card text-info"></i>' : '<span class="text-muted">—</span>'}</td>
                <td class="text-end small text-muted d-none d-md-table-cell">${formatTimestamp(sb.created_at_ms)}</td>
                <td class="text-end">
                    <button class="btn btn-sm text-muted delete-btn" onclick="event.stopPropagation(); deleteSandbox('${sb.name}')" title="Delete">
                        <i class="bi bi-trash3"></i>
                    </button>
                </td>
            </tr>
        `).join('');
    } catch (e) {
        tbody.innerHTML = `
            <tr><td colspan="7" class="text-center text-muted py-5">
                <i class="bi bi-exclamation-triangle fs-1 d-block mb-3 text-warning"></i>
                <p>Could not load sandboxes.</p>
                <p class="small">${escapeHtml(e.message)}</p>
            </td></tr>`;
    }
}

// ─── Sandbox Info Page ───────────────────────────────────────────────────────

async function loadSandboxInfo(name) {
    const container = document.getElementById('sandbox-info');
    container.innerHTML = renderSpinner();
    try {
        const [sb, pendingApprovals, policyData] = await Promise.all([
            apiFetch(`${API}/sandboxes/${name}`),
            apiFetch(`${API}/sandboxes/${name}/approvals/pending`).catch(() => []),
            apiFetch(`${API}/sandboxes/${name}/policy`).catch(() => null),
        ]);

        updateSandboxPhase(sb);

        const phaseBadge = SG.badges.phase[sb.phase] || 'text-bg-secondary';
        const policy = policyData?.policy;
        const networkCount = policy ? Object.keys(policy.network_policies || {}).length : 0;
        const pendingCount = pendingApprovals?.length || 0;

        container.innerHTML = `
            <!-- Hero Header -->
            <div class="sandbox-hero">
                <div class="d-flex align-items-center gap-3 mb-2">
                    <h3 class="mb-0">${escapeHtml(sb.name)}</h3>
                    <span class="badge ${phaseBadge}">${sb.phase}</span>
                </div>
                <div class="sandbox-meta">
                    <span class="font-monospace">${escapeHtml(sb.image || 'Default image')}</span>
                    <span>${sb.namespace || 'default'}</span>
                    ${sb.created_at_ms ? `<span>${formatTimestamp(sb.created_at_ms)}</span>` : ''}
                    ${sb.gpu ? '<span><i class="bi bi-gpu-card text-info me-1"></i>GPU</span>' : ''}
                </div>
            </div>

            <!-- Summary Cards -->
            <div class="row g-3 mb-4">
                <div class="col-md-4">
                    <a href="${gwUrl('/sandboxes/' + name + '/policy')}" class="card text-decoration-none policy-overview-card sg-card-themed h-100">
                        <div class="card-body">
                            <div class="d-flex align-items-center mb-2">
                                <i class="bi bi-shield-lock text-info me-2"></i>
                                <h6 class="mb-0">Policy</h6>
                            </div>
                            <div class="fs-2 fw-bold mb-1">${networkCount}</div>
                            <span class="text-muted small">${networkCount === 1 ? '1 network rule' : networkCount + ' network rules'} · v${sb.current_policy_version}</span>
                        </div>
                        <div class="card-footer border-0 pt-0 small" style="background:transparent">
                            Manage <i class="bi bi-arrow-right"></i>
                        </div>
                    </a>
                </div>
                <div class="col-md-4">
                    <a href="${gwUrl('/sandboxes/' + name + '/approvals')}" class="card text-decoration-none policy-overview-card sg-card-themed h-100">
                        <div class="card-body">
                            <div class="d-flex align-items-center mb-2">
                                <i class="bi bi-check-circle ${pendingCount > 0 ? 'text-warning' : 'text-success'} me-2"></i>
                                <h6 class="mb-0">Approvals</h6>
                            </div>
                            <div class="fs-2 fw-bold mb-1 ${pendingCount > 0 ? 'text-warning' : ''}">${pendingCount}</div>
                            <span class="text-muted small">${pendingCount === 0 ? 'No pending requests' : pendingCount === 1 ? '1 request needs review' : pendingCount + ' requests need review'}</span>
                        </div>
                        <div class="card-footer border-0 pt-0 small" style="background:transparent">
                            ${pendingCount > 0 ? 'Review' : 'View history'} <i class="bi bi-arrow-right"></i>
                        </div>
                    </a>
                </div>
                <div class="col-md-4">
                    <a href="${gwUrl('/sandboxes/' + name + '/logs')}" class="card text-decoration-none policy-overview-card sg-card-themed h-100">
                        <div class="card-body">
                            <div class="d-flex align-items-center mb-2">
                                <i class="bi bi-journal-text text-muted me-2"></i>
                                <h6 class="mb-0">Logs</h6>
                            </div>
                            <span class="text-muted small">Live log stream and history</span>
                        </div>
                        <div class="card-footer border-0 pt-0 small" style="background:transparent">
                            View <i class="bi bi-arrow-right"></i>
                        </div>
                    </a>
                </div>
            </div>

            <!-- Properties -->
            <h6 class="text-muted mb-3">Properties</h6>
            <dl class="row mb-0 small">
                <dt class="col-sm-2 text-muted fw-normal">ID</dt>
                <dd class="col-sm-10 font-monospace">${sb.id}</dd>
            </dl>`;

        if (typeof connectWebSocket === 'function') {
            connectWebSocket(sb.name, sb.id);
        }
    } catch (e) {
        container.innerHTML = renderError(`Sandbox "${escapeHtml(name)}" not found.`);
    }
}

function updateSandboxPhase(sb) {
    const phaseBadge = document.getElementById('ctx-sandbox-phase');
    if (phaseBadge && sb) {
        phaseBadge.className = `badge ${SG.badges.phase[sb.phase] || 'text-bg-secondary'}`;
        phaseBadge.textContent = sb.phase;
    }
}

// ─── Terminal Tab ─────────────────────────────────────────────────────────────

let _termHistory = [];
let _termHistoryIdx = -1;

function loadTerminalPage(sandboxName) {
    const container = document.getElementById('terminal-content');
    container.innerHTML = `
        <div class="d-flex gap-2 mb-2">
            <div class="input-group input-group-sm flex-grow-1">
                <span class="input-group-text font-monospace" style="background:var(--sg-card);border-color:var(--sg-border);color:var(--sg-accent)">$</span>
                <input type="text" class="form-control font-monospace" id="term-input"
                       placeholder="ls -la" autocomplete="off"
                       style="background:var(--sg-log-bg);border-color:var(--sg-border);color:var(--sg-log-text)"
                       onkeydown="handleTermKey(event, '${sandboxName}')">
            </div>
            <button class="btn btn-success btn-sm" onclick="runTermCommand('${sandboxName}')">
                <i class="bi bi-play-fill me-1"></i>Run
            </button>
            <button class="btn btn-outline-secondary btn-sm" onclick="document.getElementById('term-output').innerHTML = ''" title="Clear">
                <i class="bi bi-trash"></i>
            </button>
        </div>
        <div class="log-output font-monospace" id="term-output" style="min-height:300px"></div>`;

    document.getElementById('term-input')?.focus();
}

function handleTermKey(event, sandboxName) {
    if (event.key === 'Enter') {
        event.preventDefault();
        runTermCommand(sandboxName);
    } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        if (_termHistoryIdx < _termHistory.length - 1) {
            _termHistoryIdx++;
            document.getElementById('term-input').value = _termHistory[_termHistory.length - 1 - _termHistoryIdx];
        }
    } else if (event.key === 'ArrowDown') {
        event.preventDefault();
        if (_termHistoryIdx > 0) {
            _termHistoryIdx--;
            document.getElementById('term-input').value = _termHistory[_termHistory.length - 1 - _termHistoryIdx];
        } else {
            _termHistoryIdx = -1;
            document.getElementById('term-input').value = '';
        }
    }
}

async function runTermCommand(sandboxName) {
    const input = document.getElementById('term-input');
    const output = document.getElementById('term-output');
    const cmd = input.value.trim();
    if (!cmd) return;

    _termHistory.push(cmd);
    _termHistoryIdx = -1;
    input.value = '';
    input.disabled = true;

    output.innerHTML += `<div class="log-line" style="color:var(--sg-accent)">$ ${escapeHtml(cmd)}</div>`;
    output.scrollTop = output.scrollHeight;

    try {
        const result = await apiFetch(`${API}/sandboxes/${sandboxName}/exec`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: cmd }),
        });

        if (result.stdout) {
            output.innerHTML += `<div class="log-line" style="white-space:pre-wrap">${escapeHtml(result.stdout)}</div>`;
        }
        if (result.stderr) {
            output.innerHTML += `<div class="log-line log-error" style="white-space:pre-wrap">${escapeHtml(result.stderr)}</div>`;
        }

        if (result.exit_code !== 0) {
            output.innerHTML += `<div class="log-line log-error">exit code: ${result.exit_code}</div>`;
        }
    } catch (e) {
        output.innerHTML += `<div class="log-line log-error">Error: ${escapeHtml(e.message)}</div>`;
    }

    output.scrollTop = output.scrollHeight;
    input.disabled = false;
    input.focus();
}

// ─── Sandbox Actions ─────────────────────────────────────────────────────────

async function deleteSandbox(name) {
    const confirmed = await showConfirm(
        `Delete sandbox "${name}"? This cannot be undone.`,
        { icon: 'trash', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Delete' }
    );
    if (!confirmed) return;
    try {
        await apiFetch(`${API}/sandboxes/${name}`, { method: 'DELETE' });
        showToast(`Sandbox "${name}" deleted.`, 'success');
        navigateTo(gwUrl('/sandboxes'));
    } catch (e) {
        showToast(`Delete failed: ${e.message}`, 'danger');
    }
}
