/**
 * Shoreguard — Alpine components for the auth pages (login/register/setup/invite).
 *
 * Lifted 1:1 from the previous inline x-data="{ ... }" blocks on each auth
 * template so that the Alpine CSP build can resolve them as registered
 * components (inline object literals are rejected by the CSP parser).
 */

function loginForm() {
    return {
        email: '',
        password: '',
        error: '',
        loading: false,
        registrationEnabled: false,
        oidcProviders: [],
        nextUrl: new URLSearchParams(window.location.search).get('next') || '/',
        oidcError: new URLSearchParams(window.location.search).get('error') || '',

        async init() {
            try {
                const d = await fetch('/api/auth/check').then(r => r.json());
                if (d.registration_enabled) this.registrationEnabled = true;
                if (d.oidc_providers) this.oidcProviders = d.oidc_providers;
            } catch {}
        },

        async submit() {
            this.error = '';
            this.loading = true;
            try {
                const resp = await fetch('/api/auth/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email: this.email.trim(), password: this.password }),
                });
                if (resp.ok) {
                    let next = new URLSearchParams(window.location.search).get('next') || '/';
                    if (!next.startsWith('/') || next.startsWith('//')) next = '/';
                    window.location.href = next;
                } else {
                    const body = await resp.json().catch(() => ({}));
                    this.error = body.detail || 'Invalid credentials';
                }
            } catch {
                this.error = 'Network error — is the server running?';
            } finally {
                this.loading = false;
            }
        },

        oidcHref(providerName) {
            return '/api/auth/oidc/login/' + providerName + '?next=' + encodeURIComponent(this.nextUrl);
        },
    };
}

function registerForm() {
    return {
        email: '',
        password: '',
        confirm: '',
        error: '',
        loading: false,

        async submit() {
            this.error = '';
            if (this.password !== this.confirm) {
                this.error = 'Passwords do not match';
                return;
            }
            if (this.password.length < 8) {
                this.error = 'Password must be at least 8 characters';
                return;
            }
            this.loading = true;
            try {
                const resp = await fetch('/api/auth/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email: this.email.trim(), password: this.password }),
                });
                if (resp.ok) {
                    window.location.href = '/';
                } else {
                    const body = await resp.json().catch(() => ({}));
                    this.error = body.detail || 'Registration failed';
                }
            } catch {
                this.error = 'Network error — is the server running?';
            } finally {
                this.loading = false;
            }
        },
    };
}

function setupForm() {
    return {
        email: '',
        password: '',
        confirm: '',
        error: '',
        loading: false,

        async submit() {
            this.error = '';
            if (this.password !== this.confirm) {
                this.error = 'Passwords do not match';
                return;
            }
            if (this.password.length < 8) {
                this.error = 'Password must be at least 8 characters';
                return;
            }
            this.loading = true;
            try {
                const resp = await fetch('/api/auth/setup', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email: this.email.trim(), password: this.password }),
                });
                if (resp.ok) {
                    let next = new URLSearchParams(window.location.search).get('next') || '/';
                    if (!next.startsWith('/') || next.startsWith('//')) next = '/';
                    window.location.href = next;
                } else {
                    const body = await resp.json().catch(() => ({}));
                    this.error = body.detail || 'Setup failed';
                }
            } catch {
                this.error = 'Network error — is the server running?';
            } finally {
                this.loading = false;
            }
        },
    };
}

function inviteForm() {
    return {
        password: '',
        confirm: '',
        error: '',
        loading: false,

        async submit() {
            this.error = '';
            if (this.password !== this.confirm) {
                this.error = 'Passwords do not match';
                return;
            }
            if (this.password.length < 8) {
                this.error = 'Password must be at least 8 characters';
                return;
            }
            const token = new URLSearchParams(window.location.search).get('token');
            if (!token) {
                this.error = 'Missing invite token';
                return;
            }
            this.loading = true;
            try {
                const resp = await fetch('/api/auth/accept-invite', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ token, password: this.password }),
                });
                if (resp.ok) {
                    window.location.href = '/';
                } else {
                    const body = await resp.json().catch(() => ({}));
                    this.error = body.detail || 'Failed to accept invite';
                }
            } catch {
                this.error = 'Network error — is the server running?';
            } finally {
                this.loading = false;
            }
        },
    };
}

document.addEventListener('alpine:init', () => {
    Alpine.data('loginForm', loginForm);
    Alpine.data('registerForm', registerForm);
    Alpine.data('setupForm', setupForm);
    Alpine.data('inviteForm', inviteForm);
});
