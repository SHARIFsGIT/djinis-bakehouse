import { X } from "lucide-react";
import { useEffect } from "react";

/* One floating side drawer for the whole back office — every page that
   edits "over" its list uses this shell so they all stack, lock and close
   the same way. The drawer sits at z-[70]: above the sticky navbar (z-50,
   which otherwise paints over anything opened from inside a page) and
   below the grain overlay (80), the toasts (90) and confirm dialogs. */

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

/* The shell: fixed overlay, click-away backdrop, a header with one close
   button, and whatever the page hands over as the body (the caller's own
   scrollable flex child). Escape closes unless busy. */
export default function Drawer({ title, subtitle = null, label, onClose, busy = false, children }) {
  useScrollLock();

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  return (
    <div
      className="fixed inset-0 z-[70]"
      role="dialog"
      aria-modal="true"
      aria-label={label ?? (typeof title === "string" ? title : "Drawer")}
    >
      <div className="absolute inset-0 bg-black/40" onClick={() => !busy && onClose()} aria-hidden="true" />
      <div className="animate-pop absolute inset-y-0 right-0 flex w-full max-w-md flex-col bg-base-100 shadow-2xl">
        <div className="flex items-start justify-between gap-3 border-b border-base-300/70 px-6 py-4">
          <div className="min-w-0">
            <h2 className="font-display truncate text-xl font-semibold">{title}</h2>
            {subtitle && <p className="text-xs opacity-60">{subtitle}</p>}
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
