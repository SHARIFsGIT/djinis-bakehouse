import { X } from "lucide-react";
import { useEscape, useFocusHandoff, useScrollLock } from "./Drawer";

/* A panel that rises from the bottom edge of a phone — where a thumb
   already is — for a short task that belongs to the page behind it (the
   catalog's filters). It shares the desk drawers' manners (Drawer.jsx):
   the page behind is frozen, Escape and the scrim close it, and the
   keyboard's focus goes in with it and back out to whatever opened it.

   The panel never grows past 85% of the visible screen (dvh — the height
   actually on show, browser bars included), scrolls inside itself without
   dragging the page along, and keeps its footer clear of the home
   indicator. */
export default function Sheet({ title, onClose, footer = null, children }) {
  useScrollLock();
  useEscape(onClose);
  const panel = useFocusHandoff();

  return (
    <div className="fixed inset-0 z-[70]" role="dialog" aria-modal="true" aria-label={title}>
      <div className="fade-in absolute inset-0 bg-black/40" onClick={onClose} aria-hidden="true" />
      <div
        ref={panel}
        tabIndex={-1}
        className="sheet-up absolute inset-x-0 bottom-0 flex max-h-[85dvh] flex-col rounded-t-box bg-base-100 shadow-2xl outline-none"
      >
        <div className="flex items-center justify-between gap-3 border-b border-base-300/70 px-5 py-3.5">
          <h2 className="font-display text-lg font-semibold">{title}</h2>
          <button type="button" className="btn btn-ghost btn-sm btn-square" onClick={onClose} aria-label="Close">
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-5">{children}</div>
        {footer && (
          <div
            className="border-t border-base-300/70 px-5 pt-3.5"
            style={{ paddingBottom: "calc(0.875rem + env(safe-area-inset-bottom, 0px))" }}
          >
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
