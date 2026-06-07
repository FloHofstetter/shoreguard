/**
 * Shoreguard — Interactive Terminal (xterm.js)
 *
 * Drives a full interactive TTY session in the browser over the bidirectional
 * exec WebSocket (`/ws/{gw}/{sandbox}/exec`), backing the OpenShell
 * ExecSandboxInteractive RPC (upstream PR #1331, v0.0.57).
 *
 * Wire protocol (JSON text frames):
 *   → {type:'start', command:[...], cols, rows}   (first frame)
 *   → {type:'stdin', data:<base64>}
 *   → {type:'resize', cols, rows}
 *   ← {type:'stdout'|'stderr', data:<base64>}
 *   ← {type:'exit', exit_code}
 *   ← {type:'error', data:{message}}
 */

function _b64FromString(str) {
    const bytes = new TextEncoder().encode(str);
    let bin = '';
    bytes.forEach((b) => { bin += String.fromCharCode(b); });
    return btoa(bin);
}

function _bytesFromB64(b64) {
    const bin = atob(b64);
    const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return arr;
}

function execTerminal(sandboxName) {
    return {
        sandboxName,
        command: '/bin/bash',
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
                convertEol: false,
            });
            this._fit = new FitAddon.FitAddon();
            this._term.loadAddon(this._fit);
            this._term.open(this.$refs.terminal);
            this._fit.fit();

            this._term.onData((data) => {
                if (this._ws && this._ws.readyState === WebSocket.OPEN) {
                    this._ws.send(JSON.stringify({ type: 'stdin', data: _b64FromString(data) }));
                }
            });
            this._term.onResize(({ cols, rows }) => {
                if (this._ws && this._ws.readyState === WebSocket.OPEN) {
                    this._ws.send(JSON.stringify({ type: 'resize', cols, rows }));
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

        connect() {
            if (this.connected) return;
            const cmd = (this.command || '/bin/bash').trim();
            this._fit.fit();
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const url = `${protocol}//${window.location.host}/ws/${GW}/${this.sandboxName}/exec`;
            const ws = new WebSocket(url);
            this._ws = ws;
            this.status = 'Connecting…';

            ws.onopen = () => {
                this.connected = true;
                this.status = 'Connected';
                ws.send(JSON.stringify({
                    type: 'start',
                    command: cmd.split(/\s+/),
                    cols: this._term.cols,
                    rows: this._term.rows,
                }));
                this._term.focus();
            };
            ws.onmessage = (ev) => {
                let msg;
                try { msg = JSON.parse(ev.data); } catch { return; }
                if (msg.type === 'stdout' || msg.type === 'stderr') {
                    this._term.write(_bytesFromB64(msg.data));
                } else if (msg.type === 'exit') {
                    this._term.write(`\r\n\x1b[90m[process exited with code ${msg.exit_code}]\x1b[0m\r\n`);
                    this.disconnect();
                } else if (msg.type === 'error') {
                    const m = (msg.data && msg.data.message) || 'stream error';
                    this._term.write(`\r\n\x1b[31m[error: ${m}]\x1b[0m\r\n`);
                }
            };
            ws.onclose = () => {
                this.connected = false;
                this.status = 'Disconnected';
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

        clear() {
            if (this._term) this._term.clear();
        },
    };
}

document.addEventListener('alpine:init', () => {
    Alpine.data('execTerminal', execTerminal);
});
