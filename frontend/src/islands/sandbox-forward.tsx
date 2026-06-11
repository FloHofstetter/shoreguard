/**
 * TCP / SSH forward tunnel (island).
 *
 * Relays a raw bidirectional byte tunnel into a sandbox over the
 * forward WebSocket (`/ws/{gw}/{sandbox}/forward`, OpenShell ForwardTcp
 * RPC). For SSH targets it first mints a short-lived relay session via
 * `POST /sandboxes/{name}/ssh` and passes the token through.
 *
 * Wire protocol: a first JSON text frame ({target, host?, port?,
 * authorization_token?}) followed by raw binary frames in both directions.
 */

import { useEffect, useRef, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { API, GW } from "../lib/constants";

export default function SandboxForwardPage({ name }: { name: string }) {
  const [target, setTarget] = useState<"tcp" | "ssh">("tcp");
  const [host, setHost] = useState("127.0.0.1");
  const [port, setPort] = useState("8080");
  const [connected, setConnected] = useState(false);
  const [status, setStatus] = useState("Disconnected");
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<XtermTerminal | null>(null);
  const fitRef = useRef<{ fit(): void } | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const term = new Terminal({
      cursorBlink: true,
      fontFamily: "var(--bs-font-monospace, monospace)",
      fontSize: 13,
      theme: { background: "#0d1117", foreground: "#c9d1d9" },
    });
    const fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current!);
    fit.fit();
    term.onData((data) => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(new TextEncoder().encode(data));
      }
    });
    const onWindowResize = () => {
      try {
        fit.fit();
      } catch {
        // detached
      }
    };
    window.addEventListener("resize", onWindowResize);
    termRef.current = term;
    fitRef.current = fit;
    return () => {
      window.removeEventListener("resize", onWindowResize);
      wsRef.current?.close();
      term.dispose();
    };
  }, []);

  const disconnect = () => {
    wsRef.current?.close();
    wsRef.current = null;
    setConnected(false);
    setStatus("Disconnected");
  };

  const connect = async () => {
    if (connected) return;
    setStatus("Connecting…");
    const term = termRef.current!;

    const init: Record<string, unknown> = { target };
    if (target === "tcp") {
      const h = host.trim();
      const p = parseInt(port, 10);
      if (!h || !p) {
        setStatus("Host and port required");
        return;
      }
      init.host = h;
      init.port = p;
    } else {
      try {
        const sess = await apiFetch<{ token?: string }>(`${API}/sandboxes/${name}/ssh`, {
          method: "POST",
        });
        init.authorization_token = sess?.token ?? "";
      } catch (e) {
        setStatus(`SSH session failed: ${(e as Error).message}`);
        return;
      }
    }

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/${GW}/${name}/forward`);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      setStatus("Connected");
      ws.send(JSON.stringify(init));
      term.focus();
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === "error") {
            const m = msg.data?.message || "stream error";
            term.write(`\r\n\x1b[31m[error: ${m}]\x1b[0m\r\n`);
          }
        } catch {
          // ignore non-JSON text
        }
        return;
      }
      term.write(new Uint8Array(ev.data));
    };
    ws.onclose = () => {
      setConnected(false);
      setStatus("Disconnected");
      term.write("\r\n\x1b[90m[tunnel closed]\x1b[0m\r\n");
    };
    ws.onerror = () => setStatus("Connection error");
  };

  return (
    <div>
      <p class="text-muted small">
        Relay a raw TCP/SSH tunnel into this sandbox. <strong>SSH</strong> opens a short-lived
        relay session and streams the connection bytes; a full in-browser SSH client is a planned
        follow-up, so the SSH view shows the raw protocol stream.
      </p>
      <div class="row g-2 align-items-end mb-2">
        <div class="col-auto">
          <label class="form-label small">Target</label>
          <select
            class="form-select form-select-sm"
            disabled={connected}
            value={target}
            onChange={(e) => setTarget((e.target as HTMLSelectElement).value as "tcp" | "ssh")}
          >
            <option value="tcp">TCP</option>
            <option value="ssh">SSH</option>
          </select>
        </div>
        {target === "tcp" && (
          <div class="col-auto d-flex gap-2 align-items-end">
            <div>
              <label class="form-label small">Host</label>
              <input
                class="form-control form-control-sm"
                placeholder="127.0.0.1"
                disabled={connected}
                value={host}
                onInput={(e) => setHost((e.target as HTMLInputElement).value)}
              />
            </div>
            <div>
              <label class="form-label small">Port</label>
              <input
                type="number"
                min={1}
                max={65535}
                class="form-control form-control-sm"
                disabled={connected}
                value={port}
                onInput={(e) => setPort((e.target as HTMLInputElement).value)}
              />
            </div>
          </div>
        )}
        <div class="col-auto">
          {!connected ? (
            <button class="btn btn-success btn-sm" onClick={() => void connect()}>
              <i class="bi bi-plug me-1" />
              Connect
            </button>
          ) : (
            <button class="btn btn-outline-danger btn-sm" onClick={disconnect}>
              <i class="bi bi-x-circle me-1" />
              Disconnect
            </button>
          )}
        </div>
        <div class="col-auto">
          <span class={`badge ${connected ? "text-bg-success" : "text-bg-secondary"}`}>
            {status}
          </span>
        </div>
      </div>
      <div ref={containerRef} class="sg-surface-log sg-min-h-300" style="padding:4px;" />
    </div>
  );
}
