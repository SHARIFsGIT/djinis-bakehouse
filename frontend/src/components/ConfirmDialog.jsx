import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";

/** The house "are you sure?" — one box, so every destructive action in the
 *  back of house keeps the same shape, sizing and escape hatches (backdrop
 *  click, Escape and "keep it" all back out; nothing fires by accident).
 *
 *  It is a real dialog to a keyboard and a screen reader too: focus moves to
 *  the safe button when it opens (so Enter never confirms by reflex), comes
 *  back to whatever opened it when it closes, and the title is announced.
 *
 *  It is drawn at the end of <body>, not where it is written. A frosted
 *  card (backdrop-filter) or a drawer that slid in (transform) becomes the
 *  box that anything "fixed" inside it is laid out in — so the dialog used
 *  to open inside whichever card held the button, its scrim dimming that
 *  card alone, and half off the screen when the card was. */
export default function ConfirmDialog({
  title,
  children = null,
  confirmLabel = "Yes, remove",
  cancelLabel = "Keep it",
  busy = false,
  onConfirm,
  onCancel,
}) {
  const titleId = useId();
  const cancelRef = useRef(null);
  // Captured while rendering — before anything inside the dialog can take
  // focus — so closing hands focus back to the control that opened it.
  const opener = useRef(typeof document === "undefined" ? null : document.activeElement);

  useEffect(() => {
    cancelRef.current?.focus();
    const returnTo = opener.current;
    return () => returnTo?.focus?.();
  }, []);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "Escape" || busy) return;
      // This dialog is the top layer — a drawer beneath must not close too.
      e.stopPropagation();
      onCancel();
    };
    // Capture phase: heard before any drawer's own Escape listener.
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [busy, onCancel]);

  return createPortal(
    <div className="modal modal-open" role="alertdialog" aria-modal="true" aria-labelledby={titleId}>
      <div className="modal-box max-w-sm">
        <h3 id={titleId} className="font-display text-lg font-bold">{title}</h3>
        {children && <div className="mt-1 text-sm opacity-70">{children}</div>}
        <div className="modal-action">
          <button
            ref={cancelRef}
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={onCancel}
            disabled={busy}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            className="btn btn-error btn-sm"
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? <span className="loading loading-spinner loading-xs" /> : confirmLabel}
          </button>
        </div>
      </div>
      <div className="modal-backdrop" onClick={() => !busy && onCancel()} aria-hidden="true" />
    </div>,
    document.body,
  );
}
