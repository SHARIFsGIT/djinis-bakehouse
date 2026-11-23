/* The bakes a shopper has just looked at — kept in this browser only, so the
   way back to "that loaf from a minute ago" is one tap.

   Only what a tile needs to draw is kept: the slug, the name, the photo.
   No price and no stock — those change, and a remembered price would be a
   promise the shop never made. The tile is a way back to the page, where
   every figure comes fresh from the server. */

const KEY = "bakehouse_recent";
export const MAX_RECENT = 8;

const tile = (bake) => ({
  slug: String(bake.slug),
  name: typeof bake.name === "string" ? bake.name : "",
  image_url: typeof bake.image_url === "string" ? bake.image_url : null,
});

/** `list` with `bake` moved to the front — no duplicates, newest first,
    never more than MAX_RECENT. Pure, so it is unit-tested under node. */
export const withBake = (list, bake) => {
  // Whatever was stored is tidied on the way through, never trusted as is.
  const kept = (Array.isArray(list) ? list : [])
    .filter((b) => b && typeof b.slug === "string" && b.slug && b.slug !== bake?.slug)
    .map(tile);
  if (!bake || typeof bake.slug !== "string" || !bake.slug) return kept.slice(0, MAX_RECENT);
  return [tile(bake), ...kept].slice(0, MAX_RECENT);
};

/** What this browser remembers (empty when storage is refused or mangled). */
export function readRecent() {
  try {
    return withBake(JSON.parse(localStorage.getItem(KEY) ?? "[]"), null);
  } catch {
    return [];
  }
}

/** Put a bake at the front of the list. Returns the new list. */
export function rememberBake(bake) {
  const next = withBake(readRecent(), bake);
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Storage refused — the strip simply won't remember across visits.
  }
  return next;
}
