import { X } from "lucide-react";
import { useEffect, useRef } from "react";

/* One floating side drawer for the whole back office — every page that
   edits "over" its list uses this shell so they all stack, lock and close
   the same way. The drawer sits at z-[70]: above the sticky navbar (z-50,
   which otherwise paints over anything opened from inside a page) and
   below the grain overlay (80), confirm dialogs (DaisyUI's .modal, 999)
   and the toasts (1000 — a save's verdict must never hide behind the
   dialog that asked for it). */

/* Freeze the page behind an open drawer so the wheel doesn't scroll the
   list underneath while the admin reads or types. */
export function useScrollLock() {
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);
}

/* Escape closes an overlay — unless it is mid-save. For the drawers that
   still build their own shell (the order and enquiry rails). */
export function useEscape(onClose, busy = false) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);
}

/* An overlay takes the keyboard with it: focus moves onto the panel when it
   opens (unless something inside already asked for it — a form's first
   field) and goes back to whatever opened it when it closes. Without this
   a keyboard user is left tabbing through the page behind the scrim. */
export function useFocusHandoff() {
  const panel = useRef(null);
  // Captured while rendering, before any child can take focus.
  const opener = useRef(typeof document === "undefined" ? null : document.activeElement);
  useEffect(() => {
    const node = panel.current;
    if (node && !node.contains(document.activeElement)) node.focus();
    const returnTo = opener.current;
    return () => returnTo?.focus?.();
  }, []);
  return panel;
}

/* The shell: fixed overlay, click-away backdrop, a header with one close
   button, and whatever the page hands over as the body (the caller's own
   scrollable flex child). Escape closes unless busy. */
export default function Drawer({ title, subtitle = null, label, onClose, busy = false, children }) {
  useScrollLock();
  useEscape(onClose, busy);
  const panel = useFocusHandoff();

  return (
    <div
      className="fixed inset-0 z-[70]"
      role="dialog"
      aria-modal="true"
      aria-label={label ?? (typeof title === "string" ? title : "Drawer")}
    >
      <div className="absolute inset-0 bg-black/40" onClick={() => !busy && onClose()} aria-hidden="true" />
      <div
        ref={panel}
        tabIndex={-1}
        className="animate-pop absolute inset-y-0 right-0 flex w-full max-w-md flex-col bg-base-100 shadow-2xl outline-none"
      >
        <div className="flex items-start justify-between gap-3 border-b border-base-300/70 px-6 py-4">
          <div className="min-w-0">
            <h2 className="font-display truncate text-xl font-semibold">{title}</h2>
            {subtitle && <p className="text-xs opacity-70">{subtitle}</p>}
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm btn-square shrink-0"
            onClick={onClose}
            aria-label="Close"
            disabled={busy}
          >
            <X size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
