import { useEffect, useState } from "react";

/* A phone's bottom-edge bar that stands in for a button while that button
   is off the screen — the product page's "Add to basket", the basket's
   "Checkout" — so the page's one job is always a thumb away. This hook is
   the watching half (DockBar draws the bar): it says whether the real
   button is out of sight, and tells whatever else lives at the bottom edge
   (the back-to-top button, the toasts) how much room the bar takes, through
   the `--dock` variable on <html>.

   `key` re-arms the watcher when the button is a different element (another
   bake's page); `enabled: false` stands the bar down altogether. The bar
   also steps aside while someone is typing: where the on-screen keyboard
   shrinks the page (Android), a bar pinned to the bottom edge would sit on
   top of the keys and take a fifth of what is left of the screen. */

const NARROW = "(max-width: 639px)"; // the bar itself is hidden from `sm` up
const BAR_HEIGHT = "4.5rem";

const TYPING = "input:not([type=checkbox], [type=radio], [type=range], [type=button], [type=submit]), textarea, select, [contenteditable=true]";

export default function useDock(target, { key, enabled = true } = {}) {
  const [docked, setDocked] = useState(false);
  const [typing, setTyping] = useState(false);

  useEffect(() => {
    // On the way out of a field, look at where the focus is going (it has
    // not arrived yet): from one field to the next is still typing.
    const follow = (e) => {
      const to = e.type === "focusout" ? e.relatedTarget : e.target;
      setTyping(Boolean(to?.matches?.(TYPING)));
    };
    document.addEventListener("focusin", follow);
    document.addEventListener("focusout", follow);
    return () => {
      document.removeEventListener("focusin", follow);
      document.removeEventListener("focusout", follow);
    };
  }, []);

  useEffect(() => {
    const button = enabled ? target.current : null;
    if (!button || typeof IntersectionObserver === "undefined") {
      setDocked(false);
      return undefined;
    }
    const watcher = new IntersectionObserver(([entry]) => setDocked(!entry.isIntersecting));
    watcher.observe(button);
    return () => watcher.disconnect();
  }, [target, key, enabled]);

  const shown = docked && !typing;

  useEffect(() => {
    const root = document.documentElement;
    const narrow = window.matchMedia?.(NARROW).matches;
    if (shown && narrow) root.style.setProperty("--dock", BAR_HEIGHT);
    else root.style.removeProperty("--dock");
    return () => root.style.removeProperty("--dock");
  }, [shown]);

  return shown;
}
