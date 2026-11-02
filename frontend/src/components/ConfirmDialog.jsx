/** The house "are you sure?" — one box, so every destructive action in the
 *  back of house keeps the same shape, sizing and escape hatches (backdrop
 *  click and "keep it" both back out; nothing fires by accident). */
export default function ConfirmDialog({
  title,
  children = null,
  confirmLabel = "Yes, remove",
  cancelLabel = "Keep it",
  busy = false,
  onConfirm,
  onCancel,
}) {
  return (
    <div className="modal modal-open">
      <div className="modal-box max-w-sm">
        <h3 className="font-display text-lg font-bold">{title}</h3>
        {children && <p className="mt-1 text-sm opacity-70">{children}</p>}
        <div className="modal-action">
          <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel}>
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
      <div className="modal-backdrop" onClick={onCancel} />
    </div>
  );
}
