/**
 * Shoreguard — Reusable UI Components
 * Shared render helpers to eliminate template duplication across modules.
 */

function renderSpinner(message = 'Loading...') {
    return `<div class="text-center text-muted py-5"><div class="spinner-border spinner-border-sm me-2"></div>${escapeHtml(message)}</div>`;
}

function renderEmptyState(icon, message, action = null) {
    return `
        <div class="text-center text-muted py-5">
            <i class="bi bi-${icon} fs-1 d-block mb-3"></i>
            <p>${escapeHtml(message)}</p>
            ${action || ''}
        </div>`;
}

function renderError(message) {
    return `<div class="alert alert-danger">${escapeHtml(message)}</div>`;
}

function renderStatusBadge(status, badgeMap) {
    const cls = badgeMap[status] || 'text-bg-secondary';
    return `<span class="badge ${cls}">${escapeHtml(status)}</span>`;
}

function renderEndpointBadges(endpoints, max = 3) {
    if (!endpoints || endpoints.length === 0) return '<span class="text-muted">—</span>';
    const display = endpoints.slice(0, max);
    const moreCount = endpoints.length - max;
    let html = display.map(ep =>
        `<span class="badge endpoint-badge me-1">${escapeHtml(ep.host)}:${ep.port}</span>`
    ).join('');
    if (moreCount > 0) html += `<span class="badge text-bg-secondary">+${moreCount}</span>`;
    return html;
}

function renderGatewayTypeIcon(type) {
    const info = SG.icons.gatewayType[type];
    if (!info) return escapeHtml(type);
    return `<i class="bi bi-${info.icon} me-1"></i>${info.label}`;
}

function renderGatewayStatusBadge(gw) {
    const status = gw.status || 'offline';
    const icons = {
        connected: 'circle-fill',
        running: 'circle-fill',
        stopped: 'stop-circle',
        offline: 'circle',
    };
    const icon = icons[status] || 'circle';
    const labels = {
        connected: 'Connected',
        running: 'Running',
        stopped: 'Stopped',
        offline: 'Offline',
    };
    const label = labels[status] || status;
    const cls = SG.badges.gateway[status] || 'text-bg-secondary';
    let html = `<span class="badge ${cls}"><i class="bi bi-${icon} me-1"></i>${label}</span>`;
    if (status === 'connected' && gw.version) {
        html += ` <span class="text-muted small ms-1">${escapeHtml(gw.version)}</span>`;
    }
    return html;
}

function renderCard(title, icon, content) {
    return `
        <div class="card sg-card-themed">
            <div class="card-body">
                <h6 class="text-muted mb-3"><i class="bi bi-${icon} me-2"></i>${escapeHtml(title)}</h6>
                ${content}
            </div>
        </div>`;
}

function renderKeyValueTable(rows) {
    if (!rows || rows.length === 0) return '';
    return `
        <table class="table table-dark table-sm table-borderless mb-0">
            <tbody>
                ${rows.map(([label, value]) => `
                    <tr>
                        <td class="text-muted" style="width:140px">${escapeHtml(label)}</td>
                        <td>${value}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>`;
}
