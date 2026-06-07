/**
 * Shoreguard — Service Routing
 *
 * Lists sandbox service endpoints exposed on the gateway and lets an operator
 * expose a new loopback port or remove an endpoint. The gateway is authoritative
 * for endpoint records; ShoreGuard stores nothing locally.
 *
 * Backs the OpenShell RPCs ExposeService / GetService / ListServices /
 * DeleteService (upstream PR #1101, v0.0.57).
 */

let _exposeModal = null;

async function loadServicesPage() {
    const container = document.getElementById('services-page-content');
    container.innerHTML = renderSpinner('Loading services…');
    try {
        const resp = await apiFetch(`${API}/services`);
        const services = (resp && resp.services) || [];
        if (services.length === 0) {
            container.innerHTML = renderEmptyState(
                'hdd-network',
                'No services exposed on this gateway yet.',
            );
            return;
        }
        container.innerHTML = `
            <div class="table-responsive">
                <table class="table table-striped table-hover table-sm align-middle">
                    <thead>
                        <tr>
                            <th>Service</th>
                            <th>Sandbox</th>
                            <th>Target port</th>
                            <th>URL</th>
                            <th class="text-end">Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${services.map(renderServiceRow).join('')}
                    </tbody>
                </table>
            </div>`;
    } catch (e) {
        container.innerHTML = renderError(e.message);
    }
}

function renderServiceRow(s) {
    const url = s.url
        ? `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener" class="font-monospace small">${escapeHtml(s.url)}</a>`
        : '<span class="text-muted">—</span>';
    return `
        <tr>
            <td><strong>${escapeHtml(s.service_name)}</strong></td>
            <td class="small text-muted">${escapeHtml(s.sandbox_name)}</td>
            <td><code class="small">${s.target_port}</code></td>
            <td>${url}</td>
            <td class="text-end">
                ${_sgHasRole('operator') ? `
                <button class="btn btn-sm text-muted" data-action="delete"
                        data-sandbox="${escapeHtml(s.sandbox_name)}" data-service="${escapeHtml(s.service_name)}"
                        title="Delete">
                    <i class="bi bi-trash3"></i>
                </button>` : ''}
            </td>
        </tr>`;
}

function _ensureExposeModal() {
    if (_exposeModal) return _exposeModal;
    const el = document.createElement('div');
    el.className = 'modal fade';
    el.id = 'exposeServiceModal';
    el.tabIndex = -1;
    el.innerHTML = `
        <div class="modal-dialog">
            <div class="modal-content sg-card-themed">
                <div class="modal-header">
                    <h5 class="modal-title"><i class="bi bi-hdd-network me-2"></i>Expose service</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <form id="expose-form" class="row g-2">
                        <div class="col-md-6">
                            <label class="form-label small">Sandbox</label>
                            <input class="form-control form-control-sm" id="expose-sandbox" required>
                        </div>
                        <div class="col-md-6">
                            <label class="form-label small">Service name</label>
                            <input class="form-control form-control-sm" id="expose-service" required>
                        </div>
                        <div class="col-md-6">
                            <label class="form-label small">Target port</label>
                            <input type="number" min="1" max="65535" class="form-control form-control-sm" id="expose-port" required>
                        </div>
                        <div class="col-md-6 d-flex align-items-end">
                            <div class="form-check">
                                <input class="form-check-input" type="checkbox" id="expose-domain">
                                <label class="form-check-label small" for="expose-domain">Browser-facing URL (domain)</label>
                            </div>
                        </div>
                        <div class="col-12 text-end mt-3">
                            <button type="submit" class="btn btn-success btn-sm">
                                <i class="bi bi-plus-lg me-1"></i>Expose
                            </button>
                        </div>
                    </form>
                </div>
            </div>
        </div>`;
    document.body.appendChild(el);
    el.querySelector('#expose-form').addEventListener('submit', (e) => {
        e.preventDefault();
        _submitExpose();
    });
    _exposeModal = new bootstrap.Modal(el);
    return _exposeModal;
}

async function _submitExpose() {
    const sandbox = document.getElementById('expose-sandbox').value.trim();
    const service = document.getElementById('expose-service').value.trim();
    const target_port = parseInt(document.getElementById('expose-port').value, 10);
    const domain = document.getElementById('expose-domain').checked;
    if (!sandbox || !service || !target_port) return;
    try {
        await apiFetch(`${API}/services`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sandbox, service, target_port, domain }),
        });
        showToast(`Service "${service}" exposed.`, 'success');
        _exposeModal.hide();
        loadServicesPage();
    } catch (e) {
        showToast(`Expose failed: ${e.message}`, 'danger');
    }
}

async function deleteService(sandbox, service) {
    const confirmed = await showConfirm(
        `Delete service routing for "${sandbox}/${service}"?`,
        { icon: 'trash3', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Delete' }
    );
    if (!confirmed) return;
    try {
        await apiFetch(`${API}/services/${encodeURIComponent(sandbox)}/${encodeURIComponent(service)}`, {
            method: 'DELETE',
        });
        showToast(`Service "${service}" deleted.`, 'success');
        loadServicesPage();
    } catch (e) {
        showToast(`Delete failed: ${e.message}`, 'danger');
    }
}

function servicesPage() {
    return {
        init() {
            loadServicesPage();
            this.$el.addEventListener('click', (e) => this.handleAction(e));
        },
        refresh() { loadServicesPage(); },
        openExpose() {
            _ensureExposeModal();
            document.getElementById('expose-form').reset();
            _exposeModal.show();
        },
        handleAction(e) {
            const el = e.target.closest('[data-action]');
            if (!el) return;
            if (el.dataset.action === 'delete') deleteService(el.dataset.sandbox, el.dataset.service);
        },
    };
}

document.addEventListener('alpine:init', () => {
    Alpine.data('servicesPage', servicesPage);
});
