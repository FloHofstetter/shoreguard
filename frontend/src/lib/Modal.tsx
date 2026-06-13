/** Minimal Bootstrap-styled modal rendered by Preact (no bootstrap.js). */

import type { ComponentChildren } from "preact";
import { useEffect, useRef } from "preact/hooks";

export interface ModalProps {
  title: ComponentChildren;
  onClose: () => void;
  size?: "lg" | "sm";
  footer?: ComponentChildren;
  children: ComponentChildren;
}

export function Modal({ title, onClose, size, footer, children }: ModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  // Keep the latest onClose without re-running the mount effect each render.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCloseRef.current();
    };
    document.addEventListener("keydown", onKey);
    // Move focus into the dialog (screen readers + Esc works without a click),
    // and lock background scroll while the modal is open.
    dialogRef.current?.focus();
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, []);

  return (
    <div>
      <div
        class="modal fade show d-block"
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        onClick={(e) => {
          if (e.target === e.currentTarget) onClose();
        }}
      >
        <div
          ref={dialogRef}
          tabIndex={-1}
          class={`modal-dialog modal-dialog-centered${size ? ` modal-${size}` : ""}`}
        >
          <div class="modal-content sg-modal-themed">
            <div class="modal-header border-bottom">
              <h5 class="modal-title">{title}</h5>
              <button type="button" class="btn-close" aria-label="Close" onClick={onClose} />
            </div>
            <div class="modal-body">{children}</div>
            <div class="modal-footer border-0">
              {footer}
              <button class="btn btn-outline-secondary" onClick={onClose}>
                Close
              </button>
            </div>
          </div>
        </div>
      </div>
      <div class="modal-backdrop fade show" />
    </div>
  );
}
