/**
 * Shoreguard — Alpine.js Stores & Initialization
 *
 * Three global stores:
 *   - auth:   role, email, authenticated state
 *   - toasts: notification queue
 *   - health: gateway connectivity status
 */

document.addEventListener('alpine:init', () => {

    // ─── CSP-strict magic properties ────────────────────────────────────────
    // The @alpinejs/csp build's expression parser only supports single-variable
    // accesses and simple property chains on local scope — it cannot evaluate
    // `$store.foo.bar` chains (see alpinejs/alpine#4427). We expose every store
    // access the templates need as a dedicated magic ($isAdmin, $healthConnected,
    // …) so directives stay one-variable-deep and the CSP parser stays happy.
    //
    // Bindings: these return values (used by x-show, x-text, :class, etc.)
    Alpine.magic('isAdmin', () => Alpine.store('auth')?.hasRole('admin') ?? false);
    Alpine.magic('isOperator', () => Alpine.store('auth')?.hasRole('operator') ?? false);
    Alpine.magic('localMode', () => Alpine.store('auth')?.localMode ?? false);
    Alpine.magic('authEmail', () => Alpine.store('auth')?.email ?? null);
    Alpine.magic('authRole', () => Alpine.store('auth')?.role ?? 'viewer');
    Alpine.magic('authAuthenticated', () => Alpine.store('auth')?.authenticated ?? false);
    Alpine.magic('registrationEnabled', () => Alpine.store('auth')?.registrationEnabled ?? false);
    Alpine.magic('healthConnected', () => Alpine.store('health')?.connected ?? false);
    Alpine.magic('healthStatus', () => Alpine.store('health')?.status ?? 'unknown');
    Alpine.magic('healthGwName', () => Alpine.store('health')?.gwName ?? '');
    Alpine.magic('healthVersion', () => Alpine.store('health')?.version ?? '');
    Alpine.magic('sidebarOpen', () => Alpine.store('sidebar')?.open ?? false);
    Alpine.magic('themeMode', () => Alpine.store('theme')?.mode ?? 'dark');
    Alpine.magic('toasts', () => Alpine.store('toasts')?.items ?? []);

    // Methods: these return a function so templates can write `$method()` /
    // `$method(arg)` as a single-call directive expression.
    Alpine.magic('toggleSidebar', () => () => Alpine.store('sidebar')?.toggle());
    Alpine.magic('closeSidebar', () => () => {
        const s = Alpine.store('sidebar');
        if (s) s.open = false;
    });
    Alpine.magic('toggleTheme', () => () => Alpine.store('theme')?.toggle());
    Alpine.magic('doLogout', () => () => Alpine.store('auth')?.logout());
    Alpine.magic('checkHealth', () => () => Alpine.store('health')?.check());
    Alpine.magic('scheduleToastRemove', () => (id, delay) =>
        Alpine.store('toasts')?.scheduleRemove(id, delay));

    // Array/length helpers — the CSP parser can't evaluate chained property
    // access combined with comparison operators (e.g. `arr.length === 0`),
    // so we expose boolean helpers for the common patterns.
    Alpine.magic('empty', () => (arr) => !arr || arr.length === 0);
    Alpine.magic('notEmpty', () => (arr) => Array.isArray(arr) && arr.length > 0);
    Alpine.magic('lenLt', () => (arr, n) => (arr || []).length < n);
    Alpine.magic('lenGt', () => (arr, n) => (arr || []).length > n);
    // String/value equality — same parser limitation for `obj.prop === 'x'`.
    Alpine.magic('eq', () => (a, b) => a === b);
    Alpine.magic('neq', () => (a, b) => a !== b);
    Alpine.magic('gt', () => (a, b) => a > b);
    Alpine.magic('lt', () => (a, b) => a < b);
    Alpine.magic('gte', () => (a, b) => a >= b);
    Alpine.magic('lte', () => (a, b) => a <= b);
    // Object helper — global `Object.keys` isn't allowed in CSP expressions.
    Alpine.magic('hasKeys', () => (obj) => !!obj && Object.keys(obj).length > 0);
    // Safe property access — `gw?.status` in a directive becomes
    // `$prop(gw, 'status')` because the CSP parser treats `?.` as a literal
    // identifier character.
    Alpine.magic('prop', () => (obj, key) => obj?.[key]);
    // Length / slice helpers — chained `arr.length` and `str.substring()` are
    // not allowed in CSP expression form.
    Alpine.magic('len', () => (arr) => (arr || []).length);
    Alpine.magic('slice', () => (str, start, end) => String(str || '').slice(start, end));

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

        async logout() {
            try {
                await fetch('/api/auth/logout', { method: 'POST' });
            } finally {
                window.location.href = '/login';
            }
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

        scheduleRemove(id, delay) {
            setTimeout(() => this.remove(id), delay);
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
                    this.gwName = GW;
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
