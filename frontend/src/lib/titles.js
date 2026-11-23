/* What the browser tab (and a bookmark, and the history list) calls each
   page. One table, read by the shell on every navigation — so a page is
   named the moment it opens, and no page has to remember to name itself.
   A product page then writes its own bake's name over the generic one
   (pages/ProductDetail.jsx). */

export const SHOP_NAME = "Djini's Bakehouse";
export const DEFAULT_TITLE = `${SHOP_NAME} — Fresh from the oven`;

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
