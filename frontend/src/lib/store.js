import client from "../api/client";

/* The shop's public settings (GET /api/store): the announcement, the contact
   lines, which ways of paying exist, the money rules, the delivery area.
   Half a dozen places on any page want them — the navbar, the footer, the
   page itself — and they share one request instead of each sending their
   own. The answer is kept for a minute: long enough that a page asks once,
   short enough that a change shows up without anyone reloading the tab. */
const MAX_AGE_MS = 60_000;

let held = null; // { at, promise }
const watchers = new Set();

/** The store settings, as a promise of the plain data. */
export function fetchStore() {
  const now = Date.now();
  if (held && now - held.at < MAX_AGE_MS) return held.promise;
  const promise = client.get("/store").then(({ data }) => data);
  held = { at: now, promise };
  // A failed fetch is not worth remembering — the next asker tries again.
  promise.catch(() => {
    if (held?.promise === promise) held = null;
  });
  return promise;
}

/** Be told when the held answer has been retired (the desk saved a
    setting). Returns the function that stops the telling. */
export function onStoreChange(listener) {
  watchers.add(listener);
  return () => watchers.delete(listener);
}

/* Everything /api/store serves that can change while the app is open is
   written through the desk's Settings (PUT /api/admin/settings). A save
   there retires the held answer and tells whoever is showing it — so the
   baker who rewrites the announcement, or publishes the legal notice, sees
   the bar and the footer follow at once, not after a reload. */
client.interceptors.response.use((response) => {
  const { method, url } = response.config ?? {};
  if (method && method !== "get" && url?.startsWith("/admin/settings")) {
    held = null;
    watchers.forEach((tell) => tell());
  }
  return response;
});
