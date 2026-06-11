/** Vendored xterm.js globals (loaded via <script> tags in templates). */

interface XtermTerminal {
  cols: number;
  rows: number;
  open(el: HTMLElement): void;
  write(data: string | Uint8Array): void;
  clear(): void;
  focus(): void;
  dispose(): void;
  loadAddon(addon: unknown): void;
  onData(cb: (data: string) => void): void;
  onResize(cb: (size: { cols: number; rows: number }) => void): void;
}

declare const Terminal: new (options: Record<string, unknown>) => XtermTerminal;
declare const FitAddon: { FitAddon: new () => { fit(): void } };
