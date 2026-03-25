/**
 * Shoreguard — Logs Tab
 * Terminal-style log viewer with level toggles and text filter.
 */

let _logData = [];
let _logFilters = { info: true, warn: true, error: true, text: '' };

async function loadLogsPage(name) {
    const container = document.getElementById('logs-content');
    return _loadLogs(name, container);
}

async function loadLogsTab(name, container) {
    return _loadLogs(name, container);
}

async function _loadLogs(name, container) {
    container.innerHTML = renderSpinner('Loading logs...');
    try {
        const logs = await apiFetch(`${API}/sandboxes/${name}/logs?lines=${SG.config.logLinesDefault}`);
        _logData = logs;
        _logFilters = { info: true, warn: true, error: true, text: '' };

        container.innerHTML = `
            <div class="d-flex justify-content-between align-items-center mb-2">
                <div class="btn-group btn-group-sm" role="group">
                    <input type="checkbox" class="btn-check" id="log-filter-info" checked onchange="toggleLogLevel('info', this.checked)">
                    <label class="btn btn-outline-info" for="log-filter-info">Info</label>
                    <input type="checkbox" class="btn-check" id="log-filter-warn" checked onchange="toggleLogLevel('warn', this.checked)">
                    <label class="btn btn-outline-warning" for="log-filter-warn">Warn</label>
                    <input type="checkbox" class="btn-check" id="log-filter-error" checked onchange="toggleLogLevel('error', this.checked)">
                    <label class="btn btn-outline-danger" for="log-filter-error">Error</label>
                </div>
                <div class="d-flex gap-2">
                    <input type="text" class="form-control form-control-sm" placeholder="Filter..."
                           style="width:180px" oninput="filterLogText(this.value)">
                    <button class="btn btn-outline-secondary btn-sm" onclick="loadLogsTab('${name}', document.getElementById('detail-tab-content'))" title="Refresh">
                        <i class="bi bi-arrow-clockwise"></i>
                    </button>
                </div>
            </div>
            <div class="log-output" id="log-container">
                ${renderLogLines(logs)}
            </div>`;

        const logEl = document.getElementById('log-container');
        if (logEl) logEl.scrollTop = logEl.scrollHeight;
    } catch (e) {
        container.innerHTML = `<div class="alert alert-danger">${escapeHtml(e.message)}</div>`;
    }
}

function renderLogLines(logs) {
    const filtered = logs.filter(log => {
        const level = (log.level || 'info').toLowerCase();
        if (!_logFilters[level] && _logFilters[level] !== undefined) return false;
        if (_logFilters.text && !log.message?.toLowerCase().includes(_logFilters.text.toLowerCase())) return false;
        return true;
    });

    if (filtered.length === 0) {
        return '<div class="text-muted">No logs match the current filters.</div>';
    }

    return filtered.map(log => {
        const level = (log.level || 'info').toLowerCase();
        return `<div class="log-line log-${level}"><span class="text-muted">${formatTimestamp(log.timestamp_ms)}</span> <span class="badge text-bg-secondary me-1">${log.source || 'gateway'}</span> ${escapeHtml(log.message)}</div>`;
    }).join('');
}

function toggleLogLevel(level, checked) {
    _logFilters[level] = checked;
    const logContainer = document.getElementById('log-container');
    if (logContainer) logContainer.innerHTML = renderLogLines(_logData);
}

function filterLogText(text) {
    _logFilters.text = text;
    const logContainer = document.getElementById('log-container');
    if (logContainer) logContainer.innerHTML = renderLogLines(_logData);
}
