import { useId } from "react";
import { createPortal } from "react-dom";
import { useEscape, useFocusHandoff } from "./Drawer";

/** A centred dialog for a form that belongs to the page behind it — the
 *  desk's product editor, closing a batch. The house confirm box
 *  (ConfirmDialog) and the side drawers already behaved as dialogs; these
 *  were only drawn as one: no name for a screen reader to say, no Escape,
 *  and the keyboard left on the page underneath. Same manners as the
 *  drawers now (Drawer.jsx): Escape closes it unless it is mid-save, the
 *  scrim still closes it, and focus goes in with it and comes back out to
 *  whatever opened it. Drawn at the end of <body>, like the confirm box and
 *  for the same reason (ConfirmDialog.jsx). */
export default function Modal({ title, onClose, busy = false, size = "max-w-2xl", titleClass = "text-xl", children }) {
  const titleId = useId();
  useEscape(onClose, busy);
  const panel = useFocusHandoff();

  return createPortal(
    <div className="modal modal-open" role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <div ref={panel} tabIndex={-1} className={`modal-box outline-none ${size}`}>
        <h3 id={titleId} className={`font-display font-bold ${titleClass}`}>
          {title}
        </h3>
        {children}
      </div>
      <div className="modal-backdrop" onClick={onClose} />
    </div>,
    document.body,
  );
}
