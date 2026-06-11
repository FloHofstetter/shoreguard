/**
 * Toasts and confirm dialogs for islands.
 *
 * During the incremental migration the legacy bundle still owns the
 * page-level toast container and Bootstrap confirm modal; when its
 * globals are present we delegate so both stacks share one UX. Once
 * the legacy scripts are gone, the Preact hosts below (lazily mounted
 * into document.body) take over without any call-site changes.
 */

import { signal } from "@preact/signals";
import { render } from "preact";

import { CONFIG } from "./constants";

export type ToastKind = "success" | "danger" | "warning" | "info";

export interface ToastAction {
  label: string;
  onClick: () => void;
}

interface ToastItem {
  id: number;
  message: string;
  icon: string;
  delay: number;
  action?: ToastAction;
}

const TOAST_ICONS: Record<ToastKind, string> = {
  success: "check-circle-fill text-success",
  danger: "x-circle-fill text-danger",
  warning: "exclamation-triangle-fill text-warning",
  info: "info-circle-fill text-info",
};

const toasts = signal<ToastItem[]>([]);
let nextToastId = 0;
let toastHostMounted = false;

function removeToast(id: number): void {
  toasts.value = toasts.value.filter((t) => t.id !== id);
}

function ToastHost() {
  return (
    <div class="toast-container position-fixed bottom-0 end-0 p-3" aria-live="polite">
      {toasts.value.map((t) => (
        <div key={t.id} class="toast show" role="alert">
          <div class="toast-body d-flex align-items-center gap-2">
            <i class={`bi bi-${t.icon}`} />
            <span>{t.message}</span>
            {t.action && (
              <button
                class="btn btn-warning btn-sm ms-2"
                onClick={() => {
                  removeToast(t.id);
                  t.action!.onClick();
                }}
              >
                {t.action.label}
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function mountToastHost(): void {
  if (toastHostMounted) return;
  toastHostMounted = true;
  const root = document.createElement("div");
  document.body.appendChild(root);
  render(<ToastHost />, root);
}

type LegacyToast = (message: string, kind: string) => void;
type LegacyConfirm = (message: string, options?: ConfirmOptions) => Promise<boolean>;

export function showToast(message: string, kind: ToastKind = "info", action?: ToastAction): void {
  const legacy = (window as { showToast?: LegacyToast }).showToast;
  if (!action && typeof legacy === "function") {
    legacy(message, kind);
    return;
  }
  mountToastHost();
  const id = ++nextToastId;
  const delay = kind === "warning" ? CONFIG.approvalToastDelay : CONFIG.toastDelay;
  toasts.value = [...toasts.value, { id, message, icon: TOAST_ICONS[kind], delay, action }];
  setTimeout(() => removeToast(id), delay);
}

// ── Confirm ──────────────────────────────────────────────────────────

export interface ConfirmOptions {
  icon?: string;
  iconColor?: string;
  btnClass?: string;
  btnLabel?: string;
}

interface ConfirmRequest extends Required<ConfirmOptions> {
  message: string;
  resolve: (confirmed: boolean) => void;
}

const confirmRequest = signal<ConfirmRequest | null>(null);
let confirmHostMounted = false;

function ConfirmHost() {
  const req = confirmRequest.value;
  if (!req) return null;
  const settle = (confirmed: boolean) => {
    confirmRequest.value = null;
    req.resolve(confirmed);
  };
  return (
    <div>
      <div class="modal fade show d-block" tabIndex={-1}>
        <div class="modal-dialog modal-dialog-centered modal-sm">
          <div class="modal-content sg-modal-themed">
            <div class="modal-body text-center py-4">
              <i class={`bi bi-${req.icon} fs-1 ${req.iconColor} d-block mb-3`} />
              <p class="mb-0">{req.message}</p>
            </div>
            <div class="modal-footer border-0 justify-content-center">
              <button class="btn btn-outline-secondary btn-sm" onClick={() => settle(false)}>
                Cancel
              </button>
              <button class={`btn btn-sm ${req.btnClass}`} onClick={() => settle(true)}>
                {req.btnLabel}
              </button>
            </div>
          </div>
        </div>
      </div>
      <div class="modal-backdrop fade show" />
    </div>
  );
}

function mountConfirmHost(): void {
  if (confirmHostMounted) return;
  confirmHostMounted = true;
  const root = document.createElement("div");
  document.body.appendChild(root);
  render(<ConfirmHost />, root);
}

export function showConfirm(message: string, options: ConfirmOptions = {}): Promise<boolean> {
  const legacy = (window as { showConfirm?: LegacyConfirm }).showConfirm;
  if (typeof legacy === "function") {
    return legacy(message, options);
  }
  mountConfirmHost();
  return new Promise((resolve) => {
    confirmRequest.value = {
      message,
      icon: options.icon ?? "exclamation-triangle",
      iconColor: options.iconColor ?? "text-warning",
      btnClass: options.btnClass ?? "btn-danger",
      btnLabel: options.btnLabel ?? "Confirm",
      resolve,
    };
  });
}
