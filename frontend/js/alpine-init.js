/**
 * Shoreguard — Alpine.js Stores & Initialization
 *
 * Three global stores:
 *   - auth:   role, email, authenticated state
 *   - toasts: notification queue
 *   - health: gateway connectivity status
 */

document.addEventListener('alpine:init', () => {

    // ─── Auth Store ─────────────────────────────────────────────────────────
    Alpine.store('auth', {
        role: 'viewer',
        email: null,
        authenticated: false,
        needsSetup: false,
        registrationEnabled: false,
        localMode: false,

        async init() {
            try {
                const d = await fetch('/api/auth/check').then(r => r.json());
                if (d.needs_setup) {
                    this.needsSetup = true;
                    if (!window.location.pathname.startsWith('/setup')) {
                        window.location.href = '/setup?next=' + encodeURIComponent(window.location.pathname);
                    }
                    return;
                }
                if (d.authenticated) {
                    this.authenticated = true;
                    this.role = d.role || 'viewer';
                    this.email = d.email || null;
                    this.registrationEnabled = d.registration_enabled || false;
                    this.localMode = d.local_mode || false;
                }
                // Hide elements with data-sg-min-role
                document.querySelectorAll('[data-sg-min-role]').forEach(el => {
                    const minRole = el.getAttribute('data-sg-min-role');
                    if (!this.hasRole(minRole)) el.style.display = 'none';
                });
            } catch {
                // Auth check failed — leave defaults
            }
        },

        hasRole(minimum) {
            const ranks = { admin: 2, operator: 1, viewer: 0 };
            return (ranks[this.role] || 0) >= (ranks[minimum] || 99);
        },
    });

    // ─── Toast Store ────────────────────────────────────────────────────────
    Alpine.store('toasts', {
        items: [],
        _nextId: 0,

        show(message, type = 'info') {
            const iconMap = {
                success: 'check-circle-fill text-success',
                danger: 'x-circle-fill text-danger',
                warning: 'exclamation-triangle-fill text-warning',
                info: 'info-circle-fill text-info',
            };
            const id = ++this._nextId;
            this.items.push({
                id,
                message,
                type,
                icon: iconMap[type] || iconMap.info,
                delay: type === 'warning' ? SG.config.approvalToastDelay : SG.config.toastDelay,
            });
        },

        remove(id) {
            this.items = this.items.filter(t => t.id !== id);
        },
    });

    // ─── Health Store ───────────────────────────────────────────────────────
    Alpine.store('health', {
        connected: false,
        status: 'unknown',
        gwName: '',
        version: '',
        _interval: null,
        _initialCheck: true,

        init() {
            if (!GW) return;
            this.check();
            this._interval = setInterval(() => this.check(), SG.config.healthCheckInterval);
        },

        async check() {
            if (!GW) return;
            const API = `/api/gateways/${GW}`;
            try {
                const resp = await fetch(`${API}/health`);
                if (resp.ok) {
                    const data = await resp.json();
                    try {
                        const info = await fetch('/api/gateway/info').then(r => r.json());
                        this.gwName = info.name || '';
                    } catch { /* ignore */ }
                    this.version = data.version || '';

                    if (!this.connected) {
                        this.connected = true;
                        this.status = 'connected';
                        // Reset to normal interval
                        clearInterval(this._interval);
                        this._interval = setInterval(() => this.check(), SG.config.healthCheckInterval);
                        if (!this._initialCheck) {
                            Alpine.store('toasts').show('Gateway connected.', 'success');
                        }
                    }
                    this._initialCheck = false;
                } else {
                    throw new Error('Degraded');
                }
            } catch (e) {
                const wasDegraded = e.message === 'Degraded';
                this.status = wasDegraded ? 'degraded' : 'disconnected';

                if (this.connected) {
                    this.connected = false;
                    clearInterval(this._interval);
                    this._interval = setInterval(() => this.check(), SG.config.healthCheckFallback);
                }
            }
        },
    });

    // ─── Sidebar Store ──────────────────────────────────────────────────────
    Alpine.store('sidebar', {
        open: false,
        toggle() { this.open = !this.open; },
    });

    // ─── Theme Store ───────────────────────────────────────────────────────
    Alpine.store('theme', {
        mode: localStorage.getItem('sg-theme') || 'dark',

        init() {
            document.documentElement.setAttribute('data-bs-theme', this.mode);
        },

        toggle() {
            this.mode = this.mode === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-bs-theme', this.mode);
            localStorage.setItem('sg-theme', this.mode);
        },
    });

}); // end alpine:init


// ─── Global wrappers ────────────────────────────────────────────────────────

function showToast(message, type = 'info') {
    if (typeof Alpine !== 'undefined' && Alpine.store('toasts')) {
        Alpine.store('toasts').show(message, type);
    }
}

function _sgHasRole(minimum) {
    if (typeof Alpine !== 'undefined' && Alpine.store('auth')) {
        return Alpine.store('auth').hasRole(minimum);
    }
    return false;
}
