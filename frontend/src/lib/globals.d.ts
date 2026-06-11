/**
 * Globals provided by the legacy bundle (app.js / components.js /
 * alpine-init.js) during the incremental islands migration. Islands
 * reuse the established toast and confirm UX instead of duplicating it.
 */

declare global {
  /** Show a Bootstrap toast. Kind is a contextual color name. */
  function showToast(message: string, kind?: "success" | "danger" | "warning" | "info"): void;

  /** Show the shared confirm modal; resolves true when confirmed. */
  function showConfirm(
    message: string,
    options?: {
      icon?: string;
      iconColor?: string;
      btnClass?: string;
      btnLabel?: string;
    },
  ): Promise<boolean>;

  /** Gateway name injected by base.html for gateway-scoped pages. */
  const GW: string | undefined;
}

export {};
