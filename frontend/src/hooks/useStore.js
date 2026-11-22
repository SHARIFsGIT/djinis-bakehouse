import { useEffect, useState } from "react";
import { fetchStore, onStoreChange } from "../lib/store";

/** The shop's public settings for a part of the page that stays on screen
    (the navbar, the footer): fetched once — the request is shared with
    every other asker — and again whenever the desk changes a setting.
    `null` until the first answer; `{}` if the shop could not be reached,
    so callers render their empty state rather than wait forever. */
export default function useStore() {
  const [store, setStore] = useState(null);

  useEffect(() => {
    let mounted = true;
    const load = () =>
      fetchStore()
        .then((data) => mounted && setStore(data))
        // Keep what is already showing; only a first load has nothing to keep.
        .catch(() => mounted && setStore((current) => current ?? {}));
    load();
    const stop = onStoreChange(load);
    return () => {
      mounted = false;
      stop();
    };
  }, []);

  return store;
}
