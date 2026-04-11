/**
 * Shoreguard — New Sandbox Wizard
 * Step-by-step sandbox creation: agent type, config, policy presets, launch.
 */

let wizardState = { step: 1, agent: null, presets: new Set() };

// Community sandbox data loaded from API (openshell.yaml)
let _communitySandboxes = [];
let _sandboxTemplates = [];

function initWizard() {
    wizardState = { step: 1, agent: null, presets: new Set(), defaultProvider: '', fromTemplate: false };
    const envVars = document.getElementById('wizard-env-vars');
    if (envVars) envVars.innerHTML = '';
    _wizardLabels = {};
    const labelsContainer = document.getElementById('wizard-labels');
    if (labelsContainer) labelsContainer.innerHTML = '';
    updateWizardUI();
    _loadCommunitySandboxes();
    _loadSandboxTemplates();
}

async function _loadCommunitySandboxes() {
    if (_communitySandboxes.length > 0) return;
    try {
        _communitySandboxes = await apiFetch(`${API}/providers/community-sandboxes`);
    } catch {}
}

async function _loadSandboxTemplates() {
    try {
        _sandboxTemplates = await apiFetch('/api/sandbox-templates');
        _renderTemplateCards();
    } catch {}
}

function _renderTemplateCards() {
    const container = document.getElementById('wizard-templates');
    if (!container || _sandboxTemplates.length === 0) return;
    const categoryIcons = { ml: 'gpu-card', dev: 'code-slash', security: 'shield-lock' };
    container.innerHTML = _sandboxTemplates.map(t => {
        const icon = categoryIcons[t.category] || 'file-earmark-code';
        return `
            <div class="col">
                <div class="card sg-card-themed h-100 agent-card sg-cursor-pointer"
                     data-action="select-template" data-arg="${escapeHtml(t.name)}">
                    <div class="card-body text-center py-3">
                        <i class="bi bi-${icon} fs-3 d-block mb-2 sg-text-accent"></i>
                        <div class="fw-semibold">${escapeHtml(t.name)}</div>
                        <div class="text-muted small mt-1">${escapeHtml(t.description)}</div>
                        <span class="badge text-bg-secondary mt-2">${escapeHtml(t.category || 'general')}</span>
                    </div>
                </div>
            </div>`;
    }).join('');
}

async function selectTemplate(name) {
    try {
        const tpl = await apiFetch(`/api/sandbox-templates/${name}`);
        const sb = tpl.sandbox || {};

        // Populate wizard fields
        document.getElementById('wizard-image').value = sb.image || '';
        document.getElementById('wizard-gpu').checked = !!sb.gpu;

        // Populate env vars
        const envContainer = document.getElementById('wizard-env-vars');
        envContainer.innerHTML = '';
        if (sb.environment) {
            for (const [k, v] of Object.entries(sb.environment)) {
                addWizardEnvVar(k, v);
            }
        }

        // Populate description and labels from template
        const descEl = document.getElementById('wizard-description');
        if (descEl) descEl.value = sb.description || '';
        _wizardLabels = {};
        if (sb.labels) {
            for (const [k, v] of Object.entries(sb.labels)) _wizardLabels[k] = v;
        }
        _renderWizardLabels();

        // Populate presets
        wizardState.presets = new Set(sb.presets || []);
        wizardState.agent = name;
        wizardState.defaultProvider = '';
        wizardState.fromTemplate = true;
        wizardState.templateProviders = sb.providers || [];

        // Jump to summary (step 4)
        wizardState.step = 4;
        updateWizardUI();
        updateWizardSummary();
    } catch (e) {
        showToast(`Failed to load template: ${e.message}`, 'danger');
    }
}

function selectAgent(type, event) {
    wizardState.agent = type;
    document.querySelectorAll('.agent-card').forEach(c => c.classList.remove('selected'));
    if (event && event.currentTarget) event.currentTarget.classList.add('selected');

    const sandbox = _communitySandboxes.find(s => s.name === type);
    document.getElementById('wizard-image').value = sandbox?.image || '';
    wizardState.defaultProvider = sandbox?.default_provider || '';

    setTimeout(() => wizardNext(), SG.config.wizardStepDelay);
}

// ─── Environment Variable Helpers ────────────────────────────────────────────

let _envVarCounter = 0;

function addWizardEnvVar(key = '', value = '') {
    const container = document.getElementById('wizard-env-vars');
    const id = `env-var-${_envVarCounter++}`;
    container.insertAdjacentHTML('beforeend', `
        <div class="input-group input-group-sm mb-1" id="${id}">
            <input type="text" class="form-control env-key" placeholder="KEY" value="${escapeHtml(key)}">
            <span class="input-group-text">=</span>
            <input type="text" class="form-control env-val" placeholder="value" value="${escapeHtml(value)}">
            <button class="btn btn-outline-danger" type="button" data-action="remove-env-var">
                <i class="bi bi-x"></i>
            </button>
        </div>`);
}

function collectEnvVars() {
    const result = {};
    document.querySelectorAll('#wizard-env-vars .input-group').forEach(row => {
        const key = row.querySelector('.env-key')?.value?.trim();
        const val = row.querySelector('.env-val')?.value || '';
        if (key) result[key] = val;
    });
    return result;
}

// ─── Label Helpers ──────────────────────────────────────────────────────────

let _wizardLabels = {};

function addWizardLabel(key, value) {
    const keyEl = document.getElementById('wizard-label-key');
    const valEl = document.getElementById('wizard-label-val');
    const k = key || keyEl?.value?.trim();
    const v = value || valEl?.value?.trim() || '';
    if (!k || _wizardLabels[k] !== undefined) return;
    if (Object.keys(_wizardLabels).length >= 20) return;
    _wizardLabels[k] = v;
    _renderWizardLabels();
    if (keyEl) keyEl.value = '';
    if (valEl) valEl.value = '';
}

function removeWizardLabel(key) {
    delete _wizardLabels[key];
    _renderWizardLabels();
}

function _renderWizardLabels() {
    const container = document.getElementById('wizard-labels');
    if (!container) return;
    const entries = Object.entries(_wizardLabels);
    if (entries.length === 0) { container.innerHTML = ''; return; }
    container.innerHTML = '<div class="d-flex flex-wrap gap-1">' + entries.map(([k, v]) =>
        `<span class="badge text-bg-light border d-inline-flex align-items-center gap-1">
            <span class="font-monospace">${escapeHtml(k)}</span>
            <span class="text-muted">=</span>
            <span>${escapeHtml(v)}</span>
            <button type="button" class="btn-close btn-close-sm ms-1 sg-fs-xxs"
                    data-action="remove-label" data-arg="${escapeHtml(k)}"></button>
        </span>`
    ).join('') + '</div>';
    const input = document.getElementById('wizard-label-input');
    if (input) input.style.display = entries.length >= 20 ? 'none' : '';
}

function wizardNext() {
    if (wizardState.step < 4) {
        wizardState.step++;
        updateWizardUI();
    }
    if (wizardState.step === 2) loadWizardProviders();
    if (wizardState.step === 3) loadWizardPresets();
    if (wizardState.step === 4) updateWizardSummary();
}

async function loadWizardProviders() {
    const container = document.getElementById('wizard-provider-select');
    if (!container) return;
    container.innerHTML = '<div class="text-muted small"><div class="spinner-border spinner-border-sm me-1"></div></div>';
    try {
        const resp = await apiFetch(`${API}/providers`);
        const providers = Array.isArray(resp) ? resp : (resp.items || []);
        if (providers.length === 0) {
            container.innerHTML = `
                <div class="text-muted small py-1">
                    <i class="bi bi-info-circle me-1"></i>
                    No providers configured yet. Sandboxes auto-create providers on demand.
                </div>`;
            return;
        }
        container.innerHTML = providers.map(p => {
            const icon = typeof _getProviderIcon === 'function' ? _getProviderIcon(p.type) : '<i class="bi bi-gear me-1"></i>';
            const checked = wizardState.defaultProvider === p.name || wizardState.defaultProvider === p.type;
            return `
                <div class="form-check">
                    <input class="form-check-input wizard-provider-check" type="checkbox"
                           value="${escapeHtml(p.name)}" id="wiz-prov-${escapeHtml(p.name)}" ${checked ? 'checked' : ''}>
                    <label class="form-check-label" for="wiz-prov-${escapeHtml(p.name)}">
                        ${icon}<strong>${escapeHtml(p.name)}</strong>
                        <span class="badge text-bg-secondary ms-1">${escapeHtml(p.type)}</span>
                    </label>
                </div>`;
        }).join('');
    } catch {
        container.innerHTML = '<div class="text-muted small">Could not load providers.</div>';
    }
}

function getSelectedProviders() {
    return [...document.querySelectorAll('.wizard-provider-check:checked')].map(cb => cb.value);
}

function wizardPrev() {
    if (wizardState.step > 1) {
        wizardState.step--;
        updateWizardUI();
    }
}

function updateWizardUI() {
    for (let i = 1; i <= 4; i++) {
        const content = document.getElementById(`wizard-step-${i}`);
        const step = document.querySelector(`.wizard-step[data-step="${i}"]`);
        if (content) content.classList.toggle('d-none', i !== wizardState.step);
        if (step) {
            step.classList.toggle('active', i === wizardState.step);
            step.classList.toggle('completed', i < wizardState.step);
            const badge = step.querySelector('.badge');
            if (badge && i < wizardState.step) badge.className = 'badge rounded-pill bg-success me-2';
        }
    }
}

async function loadWizardPresets() {
    const container = document.getElementById('wizard-presets');
    container.innerHTML = '<div class="text-center text-muted py-3"><div class="spinner-border spinner-border-sm me-2"></div>Loading presets...</div>';
    try {
        const presets = await apiFetch(`${API_GLOBAL}/policies/presets`);
        if (!presets.length) {
            container.innerHTML = '<p class="text-muted"><i class="bi bi-info-circle me-1"></i>No policy presets available. The sandbox will start with the default policy.</p>';
            return;
        }
        container.innerHTML = `
            <table class="table table-sm table-hover align-middle mb-0">
                <thead>
                    <tr>
                        <th class="sg-w-40"></th>
                        <th>Preset</th>
                        <th>Description</th>
                    </tr>
                </thead>
                <tbody>
                    ${presets.map(p => `
                        <tr class="table-clickable" data-action="toggle-preset" data-arg="${escapeHtml(p.name)}">
                            <td><input class="form-check-input" type="checkbox" ${wizardState.presets.has(p.name) ? 'checked' : ''} data-action="toggle-preset-stop" data-arg="${escapeHtml(p.name)}"></td>
                            <td><strong>${escapeHtml(p.name)}</strong></td>
                            <td class="text-muted small">${escapeHtml(p.description || '')}</td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>`;
    } catch {
        container.innerHTML = '<p class="text-warning"><i class="bi bi-exclamation-triangle me-1"></i>Could not load presets. The sandbox will start with the default policy.</p>';
    }
}

function toggleWizardPreset(name, rowEl) {
    const checkbox = rowEl.querySelector('input[type="checkbox"]');
    if (wizardState.presets.has(name)) {
        wizardState.presets.delete(name);
        if (checkbox) checkbox.checked = false;
    } else {
        wizardState.presets.add(name);
        if (checkbox) checkbox.checked = true;
    }
}

function wizardCustomize() {
    wizardState.step = 2;
    updateWizardUI();
    loadWizardProviders();
}

function updateWizardSummary() {
    const name = document.getElementById('wizard-name').value || '(auto-generated)';
    const image = document.getElementById('wizard-image').value || '(default)';
    const gpu = document.getElementById('wizard-gpu').checked;
    const providers = wizardState.fromTemplate && wizardState.templateProviders?.length > 0
        ? wizardState.templateProviders
        : getSelectedProviders();
    const envVars = collectEnvVars();
    const envEntries = Object.entries(envVars);
    const presets = [...wizardState.presets];
    const description = document.getElementById('wizard-description')?.value || '';
    const labelEntries = Object.entries(_wizardLabels);

    document.getElementById('wizard-summary').innerHTML = `
        <dl class="row mb-0">
            <dt class="col-sm-3 text-muted">Agent</dt>
            <dd class="col-sm-9">${wizardState.agent || 'custom'}</dd>
            <dt class="col-sm-3 text-muted">Name</dt>
            <dd class="col-sm-9">${escapeHtml(name)}</dd>
            <dt class="col-sm-3 text-muted">Image</dt>
            <dd class="col-sm-9 font-monospace small">${escapeHtml(image)}</dd>
            <dt class="col-sm-3 text-muted">GPU</dt>
            <dd class="col-sm-9">${gpu ? '<i class="bi bi-check-circle text-success"></i> Yes' : 'No'}</dd>
            ${providers.length > 0 ? `
                <dt class="col-sm-3 text-muted">Providers</dt>
                <dd class="col-sm-9">${providers.map(p => `<span class="badge text-bg-info me-1">${escapeHtml(p)}</span>`).join('')}</dd>
            ` : ''}
            ${envEntries.length > 0 ? `
                <dt class="col-sm-3 text-muted">Env Vars</dt>
                <dd class="col-sm-9">${envEntries.map(([k, v]) => `<span class="badge text-bg-secondary me-1 font-monospace">${escapeHtml(k)}=${escapeHtml(v)}</span>`).join('')}</dd>
            ` : ''}
            ${description ? `
                <dt class="col-sm-3 text-muted">Description</dt>
                <dd class="col-sm-9">${escapeHtml(description)}</dd>
            ` : ''}
            ${labelEntries.length > 0 ? `
                <dt class="col-sm-3 text-muted">Labels</dt>
                <dd class="col-sm-9">${labelEntries.map(([k, v]) => `<span class="badge text-bg-light border font-monospace me-1">${escapeHtml(k)}=${escapeHtml(v)}</span>`).join('')}</dd>
            ` : ''}
            <dt class="col-sm-3 text-muted">Presets</dt>
            <dd class="col-sm-9">${presets.length > 0 ? presets.map(p => `<span class="badge text-bg-secondary me-1">${p}</span>`).join('') : '<span class="text-muted">None</span>'}</dd>
        </dl>
    `;
}

async function launchSandbox() {
    const name = document.getElementById('wizard-name').value;
    const image = document.getElementById('wizard-image').value;
    const gpu = document.getElementById('wizard-gpu').checked;
    const providers = wizardState.fromTemplate && wizardState.templateProviders?.length > 0
        ? wizardState.templateProviders
        : getSelectedProviders();
    const environment = collectEnvVars();
    const presets = [...wizardState.presets];
    const description = document.getElementById('wizard-description')?.value?.trim() || '';
    const labels = Object.keys(_wizardLabels).length > 0 ? { ..._wizardLabels } : undefined;

    document.getElementById('wizard-progress').classList.remove('d-none');
    document.getElementById('wizard-launch-btn').disabled = true;
    document.getElementById('wizard-back-btn').disabled = true;

    const progressBar = document.getElementById('wizard-progress-bar');
    const logEl = document.getElementById('wizard-log');

    const addLog = (msg, cls = '') => {
        logEl.insertAdjacentHTML('beforeend', `<div class="log-line ${cls}">${escapeHtml(msg)}</div>`);
        logEl.scrollTop = logEl.scrollHeight;
    };

    try {
        addLog('Creating sandbox...');
        if (presets.length > 0) addLog(`Presets: ${presets.join(', ')}`);
        progressBar.style.width = '20%';

        const response = await apiFetch(`${API}/sandboxes`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name, image, gpu,
                providers: providers.length > 0 ? providers : undefined,
                environment: Object.keys(environment).length > 0 ? environment : undefined,
                presets: presets.length > 0 ? presets : undefined,
                description: description || undefined,
                labels,
            }),
        });

        addLog('Sandbox submitted, waiting for ready state...');

        const op = await pollOperation(response.operation_id, {
            onProgress: (pct, msg) => {
                progressBar.style.width = `${Math.max(20, pct)}%`;
                if (msg) addLog(msg);
            },
        });

        if (op.status === 'failed') {
            throw new Error(op.error || 'Sandbox creation failed');
        }

        const sandbox = op.result;
        addLog(`Sandbox "${sandbox.name}" created.`, 'log-info');

        if (sandbox.presets_applied?.length > 0) {
            addLog(`Presets applied: ${sandbox.presets_applied.join(', ')}`, 'log-info');
        }
        if (sandbox.presets_failed?.length > 0) {
            for (const f of sandbox.presets_failed) {
                addLog(`Warning: preset "${f.preset}" failed: ${f.error}`, 'log-warn');
            }
        }
        if (sandbox.preset_error) {
            addLog(`Warning: ${sandbox.preset_error}`, 'log-warn');
        }
        if (sandbox.preset_warning) {
            addLog(`Warning: ${sandbox.preset_warning}`, 'log-warn');
        }

        progressBar.style.width = '100%';
        progressBar.classList.remove('progress-bar-animated');
        progressBar.classList.add('bg-success');

        logEl.insertAdjacentHTML('beforeend', `
            <div class="mt-3 pt-3 border-top border-secondary">
                <div class="d-flex align-items-center mb-3">
                    <i class="bi bi-check-circle-fill text-success fs-4 me-2"></i>
                    <strong>Sandbox "${escapeHtml(sandbox.name)}" is running.</strong>
                </div>
                <div class="d-flex gap-2">
                    <button class="btn btn-success btn-sm" data-action="nav" data-arg="/sandboxes/${escapeHtml(sandbox.name)}">
                        <i class="bi bi-box-arrow-in-right me-1"></i>Open Sandbox
                    </button>
                    <button class="btn btn-outline-light btn-sm" data-action="nav" data-arg="/sandboxes">
                        <i class="bi bi-grid me-1"></i>Sandboxes
                    </button>
                    <button class="btn btn-outline-light btn-sm" data-action="nav" data-arg="/wizard">
                        <i class="bi bi-plus-circle me-1"></i>Create Another
                    </button>
                </div>
            </div>`);
        logEl.scrollTop = logEl.scrollHeight;
    } catch (e) {
        addLog(`Error: ${e.message}`, 'log-error');
        progressBar.classList.add('bg-danger');
        progressBar.classList.remove('progress-bar-animated');

        logEl.insertAdjacentHTML('beforeend', `
            <div class="mt-3 pt-3 border-top border-secondary">
                <div class="d-flex gap-2">
                    <button class="btn btn-outline-light btn-sm" data-action="nav" data-arg="/wizard">
                        <i class="bi bi-arrow-clockwise me-1"></i>Try Again
                    </button>
                    <button class="btn btn-outline-light btn-sm" data-action="nav" data-arg="/sandboxes">
                        <i class="bi bi-grid me-1"></i>Sandboxes
                    </button>
                </div>
            </div>`);
        logEl.scrollTop = logEl.scrollHeight;
    }
}

// ─── Alpine component (CSP-strict dispatcher) ───────────────────────────────
// The Alpine CSP expression parser only permits method calls on the enclosing
// component, so wizard.html needs a wrapping x-data="sandboxWizard" with thin
// wrapper methods that delegate to the existing global functions. The same
// component's `handleAction($event)` listener dispatches data-action markers
// from innerHTML-injected buttons (templates, presets, labels, env vars,
// post-launch nav buttons) — no full declarative rewrite needed.
function sandboxWizard() {
    return {
        init() {
            // Delegated click handler for data-action markers in innerHTML
            // renderers. The Alpine CSP expression parser rejects `@click=
            // "handleAction($event)"` on the root, so we wire it here in JS.
            this.$el.addEventListener('click', (e) => this.handleAction(e));
        },
        selectAgent(type, e) { selectAgent(type, e); },
        next() { wizardNext(); },
        prev() { wizardPrev(); },
        customize() { wizardCustomize(); },
        launch() { launchSandbox(); },
        addEnv() { addWizardEnvVar(); },
        addLabel() { addWizardLabel(); },
        labelKeyDown(e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                addWizardLabel();
            }
        },
        handleAction(e) {
            const el = e.target.closest('[data-action]');
            if (!el) return;
            const action = el.dataset.action;
            const arg = el.dataset.arg;
            if (action === 'select-agent') {
                selectAgent(arg, { currentTarget: el });
            } else if (action === 'select-template') {
                selectTemplate(arg);
            } else if (action === 'remove-env-var') {
                const row = el.closest('.input-group');
                if (row) row.remove();
            } else if (action === 'remove-label') {
                removeWizardLabel(arg);
            } else if (action === 'toggle-preset') {
                toggleWizardPreset(arg, el.closest('tr'));
            } else if (action === 'toggle-preset-stop') {
                e.stopPropagation();
                toggleWizardPreset(arg, el.closest('tr'));
            } else if (action === 'nav') {
                navigateTo(gwUrl(arg));
            }
        },
    };
}

document.addEventListener('alpine:init', () => {
    Alpine.data('sandboxWizard', sandboxWizard);
});

document.addEventListener('DOMContentLoaded', initWizard);
