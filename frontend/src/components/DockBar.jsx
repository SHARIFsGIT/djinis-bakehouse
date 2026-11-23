/** The bar a phone gets at its bottom edge while the page's own button is
 *  out of sight (see `useDock`, which decides when). Phones only — from
 *  `sm` up the button is never far. It keeps clear of the home indicator,
 *  and of the notch when the phone lies on its side. */
export default function DockBar({ label, children }) {
  return (
    <div
      role="region"
      aria-label={label}
      className="glass sheet-up no-print fixed inset-x-0 bottom-0 z-40 border-t border-base-300 shadow-[0_-8px_24px_-12px_rgb(0_0_0/0.25)] sm:hidden"
      style={{
        paddingBottom: "env(safe-area-inset-bottom, 0px)",
        paddingLeft: "env(safe-area-inset-left, 0px)",
        paddingRight: "env(safe-area-inset-right, 0px)",
      }}
    >
      <div className="flex items-center gap-3 px-4 py-2.5">{children}</div>
    </div>
  );
}
