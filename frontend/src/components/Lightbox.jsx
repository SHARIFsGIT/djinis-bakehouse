import { X } from "lucide-react";
import { useEscape, useFocusHandoff, useScrollLock } from "./Drawer";

/* A photo, as large as the screen allows. Tap the photo's surroundings,
   the close button or Escape to put it away; the page behind is frozen and
   the keyboard's focus comes back to the photo that opened it. */
export default function Lightbox({ src, alt, onClose }) {
  useScrollLock();
  useEscape(onClose);
  const panel = useFocusHandoff();

  return (
    <div
      ref={panel}
      tabIndex={-1}
      className="fade-in fixed inset-0 z-[70] flex items-center justify-center bg-black/85 p-3 outline-none sm:p-8"
      role="dialog"
      aria-modal="true"
      aria-label={`${alt}, enlarged`}
      onClick={onClose}
    >
      <img
        src={src}
        alt={alt}
        // The photo itself is not a way out — only what surrounds it.
        onClick={(e) => e.stopPropagation()}
        className="max-h-full max-w-full rounded-box object-contain shadow-2xl"
        style={{ maxHeight: "calc(100dvh - 4rem)" }}
      />
      <button
        type="button"
        className="btn btn-circle absolute top-3 right-3 border-0 bg-base-100 text-base-content shadow-lg"
        style={{ top: "calc(0.75rem + env(safe-area-inset-top, 0px))" }}
        onClick={onClose}
        aria-label="Close the photo"
      >
        <X size={18} aria-hidden="true" />
      </button>
    </div>
  );
}
