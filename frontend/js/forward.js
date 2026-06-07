/**
 * Shoreguard — TCP / SSH Forward tunnel
 *
 * Relays a raw bidirectional byte tunnel into a sandbox over the forward
 * WebSocket (`/ws/{gw}/{sandbox}/forward`), backing the OpenShell ForwardTcp RPC
 * (upstream PR #1029, v0.0.57). For SSH targets it first mints a short-lived
 * relay session via `POST /sandboxes/{name}/ssh` and passes the token through.
 *
 * Wire protocol: a first JSON text frame ({target, host?, port?,
 * authorization_token?}) followed by raw binary frames in both directions.
 */

function forwardTunnel(sandboxName) {
    return {
        sandboxName,
        target: 'tcp',
        host: '127.0.0.1',
        port: 8080,
        connected: false,
        status: 'Disconnected',
        _term: null,
        _fit: null,
        _ws: null,
        _onResizeWindow: null,

        init() {
            this._term = new Terminal({
                cursorBlink: true,
                fontFamily: 'var(--bs-font-monospace, monospace)',
                fontSize: 13,
                theme: { background: '#0d1117', foreground: '#c9d1d9' },
            });
            this._fit = new FitAddon.FitAddon();
            this._term.loadAddon(this._fit);
            this._term.open(this.$refs.terminal);
            this._fit.fit();

            this._term.onData((data) => {
                if (this._ws && this._ws.readyState === WebSocket.OPEN) {
                    this._ws.send(new TextEncoder().encode(data));
                }
            });
            this._onResizeWindow = () => { try { this._fit.fit(); } catch (e) { /* detached */ } };
            window.addEventListener('resize', this._onResizeWindow);
        },

        destroy() {
            window.removeEventListener('resize', this._onResizeWindow);
            this.disconnect();
            if (this._term) this._term.dispose();
        },

        async connect() {
            if (this.connected) return;
            this.status = 'Connecting…';

            const init = { target: this.target };
            if (this.target === 'tcp') {
                init.host = (this.host || '').trim();
                init.port = parseInt(this.port, 10);
                if (!init.host || !init.port) { this.status = 'Host and port required'; return; }
            } else {
                // SSH: mint a short-lived relay session and forward its token.
                try {
                    const sess = await apiFetch(`${API}/sandboxes/${this.sandboxName}/ssh`, { method: 'POST' });
                    init.authorization_token = (sess && sess.token) || '';
                } catch (e) {
                    this.status = `SSH session failed: ${e.message}`;
                    return;
                }
            }

            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const url = `${protocol}//${window.location.host}/ws/${GW}/${this.sandboxName}/forward`;
            const ws = new WebSocket(url);
            ws.binaryType = 'arraybuffer';
            this._ws = ws;

            ws.onopen = () => {
                this.connected = true;
                this.status = 'Connected';
                ws.send(JSON.stringify(init));
                this._term.focus();
            };
            ws.onmessage = (ev) => {
                if (typeof ev.data === 'string') {
                    try {
                        const msg = JSON.parse(ev.data);
                        if (msg.type === 'error') {
                            const m = (msg.data && msg.data.message) || 'stream error';
                            this._term.write(`\r\n\x1b[31m[error: ${m}]\x1b[0m\r\n`);
                        }
                    } catch { /* ignore non-JSON text */ }
                    return;
                }
                this._term.write(new Uint8Array(ev.data));
            };
            ws.onclose = () => {
                this.connected = false;
                this.status = 'Disconnected';
                this._term.write('\r\n\x1b[90m[tunnel closed]\x1b[0m\r\n');
            };
            ws.onerror = () => { this.status = 'Connection error'; };
        },

        disconnect() {
            if (this._ws) {
                try { this._ws.close(); } catch (e) { /* already closed */ }
                this._ws = null;
            }
            this.connected = false;
            this.status = 'Disconnected';
        },
    };
}

document.addEventListener('alpine:init', () => {
    Alpine.data('forwardTunnel', forwardTunnel);
});
