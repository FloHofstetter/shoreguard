/**
 * Interactive sandbox terminal (island).
 *
 * Drives a full TTY session over the bidirectional exec WebSocket
 * (`/ws/{gw}/{sandbox}/exec`, OpenShell ExecSandboxInteractive RPC).
 * xterm.js and its fit addon are loaded as vendored globals by the
 * page template; this island only orchestrates them.
 *
 * Wire protocol (JSON text frames):
 *   → {type:'start', command:[...], cols, rows}   (first frame)
 *   → {type:'stdin', data:<base64>}
 *   → {type:'resize', cols, rows}
 *   ← {type:'stdout'|'stderr', data:<base64>}
 *   ← {type:'exit', exit_code}
 *   ← {type:'error', data:{message}}
 */

import { useEffect, useRef, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { API, GW } from "../lib/constants";
import { pollOperation } from "../lib/operations";

function b64FromString(str: string): string {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  bytes.forEach((b) => {
    bin += String.fromCharCode(b);
  });
  return btoa(bin);
}

function bytesFromB64(b64: string): Uint8Array {
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return arr;
}

function InteractiveTerminal({ sandboxName }: { sandboxName: string }) {
  const [command, setCommand] = useState("/bin/bash");
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
      convertEol: false,
    });
    const fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current!);
    fit.fit();
    term.onData((data) => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "stdin", data: b64FromString(data) }));
      }
    });
    term.onResize(({ cols, rows }) => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "resize", cols, rows }));
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

  const connect = () => {
    if (connected) return;
    const term = termRef.current!;
    const cmd = (command || "/bin/bash").trim();
    fitRef.current?.fit();
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/${GW}/${sandboxName}/exec`);
    wsRef.current = ws;
    setStatus("Connecting…");

    ws.onopen = () => {
      setConnected(true);
      setStatus("Connected");
      ws.send(
        JSON.stringify({
          type: "start",
          command: cmd.split(/\s+/),
          cols: term.cols,
          rows: term.rows,
        }),
      );
      term.focus();
    };
    ws.onmessage = (ev) => {
      let msg: { type: string; data?: never; exit_code?: number };
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg.type === "stdout" || msg.type === "stderr") {
        term.write(bytesFromB64(msg.data as unknown as string));
      } else if (msg.type === "exit") {
        term.write(`\r\n\x1b[90m[process exited with code ${msg.exit_code}]\x1b[0m\r\n`);
        disconnect();
      } else if (msg.type === "error") {
        const m = (msg.data as { message?: string } | undefined)?.message || "stream error";
        term.write(`\r\n\x1b[31m[error: ${m}]\x1b[0m\r\n`);
      }
    };
    ws.onclose = () => {
      setConnected(false);
      setStatus("Disconnected");
    };
    ws.onerror = () => setStatus("Connection error");
  };

  return (
    <div>
      <div class="d-flex gap-2 mb-2 align-items-center">
        <div class="input-group input-group-sm flex-grow-1">
          <span class="input-group-text font-monospace sg-surface-card">cmd</span>
          <input
            type="text"
            class="form-control font-monospace sg-surface-log"
            placeholder="/bin/bash"
            autocomplete="off"
            disabled={connected}
            value={command}
            onInput={(e) => setCommand((e.target as HTMLInputElement).value)}
          />
        </div>
        {!connected ? (
          <button class="btn btn-success btn-sm" onClick={connect}>
            <i class="bi bi-plug me-1" />
            Connect
          </button>
        ) : (
          <button class="btn btn-outline-danger btn-sm" onClick={disconnect}>
            <i class="bi bi-x-circle me-1" />
            Disconnect
          </button>
        )}
        <button
          class="btn btn-outline-secondary btn-sm"
          title="Clear"
          onClick={() => termRef.current?.clear()}
        >
          <i class="bi bi-trash" />
        </button>
        <span class={`badge ${connected ? "text-bg-success" : "text-bg-secondary"}`}>{status}</span>
      </div>
      <div ref={containerRef} class="sg-surface-log sg-min-h-300" style="padding:4px;" />
    </div>
  );
}

interface OutputLine {
  text: string;
  cls?: string;
  css?: string;
}

function OneOffRunner({ sandboxName }: { sandboxName: string }) {
  const [commandInput, setCommandInput] = useState("");
  const [outputLines, setOutputLines] = useState<OutputLine[]>([]);
  const [running, setRunning] = useState(false);
  const historyRef = useRef<string[]>([]);
  const historyIdxRef = useRef(-1);
  const outputRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const scrollOutput = () => {
    requestAnimationFrame(() => {
      const el = outputRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    });
  };

  const appendLines = (lines: OutputLine[]) => {
    setOutputLines((prev) => [...prev, ...lines]);
    scrollOutput();
  };

  const runCommand = async () => {
    const cmd = commandInput.trim();
    if (!cmd) return;
    historyRef.current.push(cmd);
    historyIdxRef.current = -1;
    setCommandInput("");
    setRunning(true);
    appendLines([{ text: `$ ${cmd}`, css: "color:var(--sg-accent)" }]);
    try {
      const response = await apiFetch<{ operation_id: string }>(
        `${API}/sandboxes/${sandboxName}/exec`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ command: cmd }),
        },
      );
      const op = await pollOperation(response.operation_id);
      if (op.status === "failed") {
        appendLines([{ text: `Error: ${op.error || "Command failed"}`, cls: "log-error" }]);
      } else {
        const result = (op.result ?? {}) as {
          stdout?: string;
          stderr?: string;
          exit_code?: number;
        };
        const lines: OutputLine[] = [];
        if (result.stdout) lines.push({ text: result.stdout, css: "white-space:pre-wrap" });
        if (result.stderr) {
          lines.push({ text: result.stderr, css: "white-space:pre-wrap", cls: "log-error" });
        }
        if (result.exit_code !== 0) {
          lines.push({ text: `exit code: ${result.exit_code}`, cls: "log-error" });
        }
        appendLines(lines);
      }
    } catch (e) {
      appendLines([{ text: `Error: ${(e as Error).message}`, cls: "log-error" }]);
    }
    setRunning(false);
    requestAnimationFrame(() => inputRef.current?.focus());
  };

  const handleKey = (event: KeyboardEvent) => {
    const history = historyRef.current;
    if (event.key === "Enter") {
      event.preventDefault();
      void runCommand();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (historyIdxRef.current < history.length - 1) {
        historyIdxRef.current++;
        setCommandInput(history[history.length - 1 - historyIdxRef.current]);
      }
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      if (historyIdxRef.current > 0) {
        historyIdxRef.current--;
        setCommandInput(history[history.length - 1 - historyIdxRef.current]);
      } else {
        historyIdxRef.current = -1;
        setCommandInput("");
      }
    }
  };

  return (
    <div class="mt-2">
      <div class="d-flex gap-2 mb-2">
        <div class="input-group input-group-sm flex-grow-1">
          <span class="input-group-text font-monospace sg-surface-card">$</span>
          <input
            ref={inputRef}
            type="text"
            class="form-control font-monospace sg-surface-log"
            placeholder="ls -la"
            autocomplete="off"
            disabled={running}
            value={commandInput}
            onInput={(e) => setCommandInput((e.target as HTMLInputElement).value)}
            onKeyDown={handleKey}
          />
        </div>
        <button class="btn btn-success btn-sm" onClick={() => void runCommand()} disabled={running}>
          <i class="bi bi-play-fill me-1" />
          Run
        </button>
        <button
          class="btn btn-outline-secondary btn-sm"
          title="Clear"
          onClick={() => setOutputLines([])}
        >
          <i class="bi bi-trash" />
        </button>
      </div>
      <div ref={outputRef} class="log-output font-monospace sg-min-h-300">
        {outputLines.map((line, i) => (
          <div key={i} class={`log-line ${line.cls || ""}`} style={line.css || ""}>
            {line.text}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function SandboxTerminalPage({ name }: { name: string }) {
  return (
    <div>
      <InteractiveTerminal sandboxName={name} />
      <details class="mt-3">
        <summary class="text-muted small sg-cursor-pointer">
          Run a one-off command (non-interactive)
        </summary>
        <OneOffRunner sandboxName={name} />
      </details>
    </div>
  );
}
