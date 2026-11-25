/* What the browser tab (and a bookmark, and the history list) calls each
   page. One table, read by the shell on every navigation — so a page is
   named the moment it opens, and no page has to remember to name itself.
   A product page then writes its own bake's name over the generic one
   (pages/ProductDetail.jsx). */

export const SHOP_NAME = "Djini's Bakehouse";
export const DEFAULT_TITLE = `${SHOP_NAME} — Fresh from the oven`;
export const DEFAULT_DESCRIPTION = `${SHOP_NAME} — breads, pastries and more, to order online.`;
export const DESCRIPTION_LIMIT = 160;

/* The server writes each page's first head (backend/app/seo.py) with the
   same table and the same rules as below — a crawler's first answer and the
   page it renders must say the same thing. Change one, change both: the
   backend's tests/test_seo.py reads this file and fails if they part. */

/* First match wins; a pattern ending in "/" also covers what is under it. */
const PAGES = [
  ["/products", "Explore our bakes"],
  ["/cart", "Your basket"],
  ["/checkout", "Checkout"],
  ["/bulk", "Bulk & event orders"],
  ["/contact", "Find us"],
  ["/login", "Log in"],
  ["/register", "Create an account"],
  ["/forgot-password", "Forgot your password"],
  ["/reset-password", "Choose a new password"],
  ["/unsubscribe", "Unsubscribe"],
  ["/privacy", "Privacy policy"],
  ["/terms", "Terms of service"],
  ["/refunds", "Refunds & cancellations"],
  ["/imprint", "Legal notice"],
  ["/impressum", "Legal notice"],
  ["/account/saved", "Saved bakes"],
  ["/account/addresses", "Address book"],
  ["/account/profile", "Your profile"],
  ["/account", "Your orders"],
  ["/admin/orders", "Desk: Orders"],
  ["/admin/products", "Desk: Products"],
  ["/admin/costing", "Desk: Costing"],
  ["/admin/reviews", "Desk: Reviews"],
  ["/admin/promotions", "Desk: Promotions"],
  ["/admin/users", "Desk: Customers"],
  ["/admin/marketing", "Desk: Marketing"],
  ["/admin/settings", "Desk: Settings"],
  ["/admin", "Baker's desk"],
];

/** The tab title for a path ("/account/orders/12" → "Your orders — Djini's
    Bakehouse"). The home page and anything unknown keep the shop's own line. */
export const titleFor = (pathname) => {
  const path = String(pathname ?? "/").replace(/\/+$/, "") || "/";
  if (path === "/") return DEFAULT_TITLE;
  const match = PAGES.find(([prefix]) => path === prefix || path.startsWith(`${prefix}/`));
  return match ? `${match[1]} — ${SHOP_NAME}` : DEFAULT_TITLE;
};

/** A shelf's own tab title ("Breads — Djini's Bakehouse"). */
export const shelfTitle = (name) => `${name} — ${SHOP_NAME}`;

const fold = (text) => String(text ?? "").split(/\s+/).filter(Boolean).join(" ");

/** Whitespace folded; past `limit`, cut at a word and closed with an
    ellipsis — so a search snippet never ends mid-word. */
export const clipDescription = (text, limit = DESCRIPTION_LIMIT) => {
  const folded = fold(text);
  if (folded.length <= limit) return folded;
  let cut = folded.slice(0, limit - 1);
  const space = cut.lastIndexOf(" ");
  if (space > Math.floor(limit / 2)) cut = cut.slice(0, space);
  return `${cut.replace(/[ ,;:—-]+$/, "")}…`;
};

/** A product page's description: the bake's own words, then its shelf and
    the shop's name when that still fits. */
export const productDescription = (description, category) => {
  let own = fold(description);
  if (own && !".!?…".includes(own.at(-1))) own += ".";
  if (category) {
    const longer = `${own} ${category} from ${SHOP_NAME}.`.trim();
    if (longer.length <= DESCRIPTION_LIMIT) return longer;
  }
  return own ? clipDescription(own) : DEFAULT_DESCRIPTION;
};
