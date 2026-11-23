import { ArrowUp } from "lucide-react";
import { useEffect, useState } from "react";

/* The long pages (the home page on a phone, a full shelf) get a way back up
   once the shopper is a couple of screens down. It stays out of the way of
   whatever a page docks to the bottom edge: a page with a docked bar sets
   --dock on <html> (hooks/useDock.js) and this button sits above it. */
export default function BackToTop() {
  const [shown, setShown] = useState(false);

  useEffect(() => {
    let frame = 0;
    const measure = () => {
      frame = 0;
      setShown(window.scrollY > window.innerHeight * 1.5);
    };
    // One measurement per painted frame, however fast the wheel spins.
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(measure);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    measure();
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  // The toasts share this corner: while the button is out, they stack above
  // it (context/ToastContext.jsx reads --top-button).
  useEffect(() => {
    const root = document.documentElement;
    if (shown) root.style.setProperty("--top-button", "3.25rem");
    else root.style.removeProperty("--top-button");
    return () => root.style.removeProperty("--top-button");
  }, [shown]);

  if (!shown) return null;
  return (
    <button
      type="button"
      onClick={() => window.scrollTo({ top: 0 })}
      className="fade-in btn btn-circle no-print fixed right-4 z-40 border border-base-300 bg-base-100 text-base-content shadow-lg"
      style={{ bottom: "calc(1.25rem + env(safe-area-inset-bottom, 0px) + var(--dock, 0px))" }}
      aria-label="Back to the top of the page"
      title="Back to top"
    >
      <ArrowUp size={18} aria-hidden="true" />
    </button>
  );
}
