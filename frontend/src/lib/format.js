/** Shared formatting helpers and domain constants. */

/* The shop's money rules. EUR-only since the 2026-11-05 cutover: the
   currency defaults to euro and the old USD figures (8.75% tax, $5.99
   delivery, $50 free-delivery threshold) are NOT approved euro values, so
   they ship as null — nothing here may quietly render as one. App.jsx
   applies the live values from /store at boot; each rule stays null until
   the owner approves an EUR figure, and the surfaces that read them (tax
   label, delivery nudge) hide rather than guess, mirroring the server's
   own fail-closed checkout pause. Charged figures never live here at all
   — they come from POST /api/orders/quote. */
export const shopConfig = {
  currency: "EUR",
  taxRate: null,
  shippingFee: null,
  freeShippingThreshold: null,
};

export const applyShopConfig = (data = {}) => {
  if (data.currency) shopConfig.currency = String(data.currency).toUpperCase();
  for (const [key, value] of [
    ["taxRate", data.tax_rate],
    ["shippingFee", data.shipping_fee],
    ["freeShippingThreshold", data.free_shipping_threshold],
  ]) {
    // null is the server saying "no approved figure", not "zero" — honour
    // it (clearing any stale value) instead of letting Number(null) === 0
    // turn a pending rule into a promised freebie.
    if (value === null || value === undefined) {
      shopConfig[key] = null;
      continue;
    }
    const n = Number(value);
    if (Number.isFinite(n) && n >= 0) shopConfig[key] = n;
  }
};

const formatters = new Map();

/* Only real ISO 4217 codes reach Intl — it accepts any well-formed
   three-letter code and throws RangeError on everything else, so validity
   comes from the runtime's own currency list, never from shape-matching
   a regex. The backend parks unreadable codes in an "unknown" bucket;
   that money must render as what it is, never dressed as $ or €. */
const runtimeCurrencyCodes =
  typeof Intl.supportedValuesOf === "function"
    ? new Set(Intl.supportedValuesOf("currency"))
    : null;

const plainFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const currencyFormatter = (code) => {
  if (runtimeCurrencyCodes ? !runtimeCurrencyCodes.has(code) : !code) {
    return null;
  }
  if (!formatters.has(code)) {
    try {
      formatters.set(
        code,
        new Intl.NumberFormat("en-US", { style: "currency", currency: code })
      );
    } catch {
      // A runtime without the code list still gets the constructor's own
      // well-formedness check — a bad code can never reach format().
      return null;
    }
  }
  return formatters.get(code);
};

/** Money in the order's own currency when one is known, else the shop's.
    An unusable code ("unknown", a corrupted row) renders as a plain
    two-decimal figure with the raw code beside it — shown honestly, never
    as USD or EUR, and never a crash. */
export const money = (n, currency) => {
  const code =
    currency === undefined || currency === null
      ? String(shopConfig.currency || "EUR").toUpperCase()
      : String(currency).trim().toUpperCase();
  const value = Number(n ?? 0);
  const formatter = currencyFormatter(code);
  if (formatter) return formatter.format(value);
  const plain = plainFormatter.format(value);
  return code ? `${plain} ${code}` : plain;
};

/** True when any listed currency code isn't the shop's active one — the
    signal that history's money needs its own visible split (dashboard
    revenue buckets, a customer's lifetime spend). One bucket is enough:
    after a cutover, the old currency's book must not disappear behind a
    zero headline. Case-insensitive — the API speaks lowercase, the store
    config upper. */
export const hasNonActiveCurrency = (codes, active = shopConfig.currency) => {
  const activeCode = String(active ?? "").trim().toLowerCase();
  return (Array.isArray(codes) ? codes : []).some(
    (code) => String(code ?? "").trim().toLowerCase() !== activeCode
  );
};

/** A timestamp from the API as the moment it names. The server keeps and
    sends UTC without saying so ("2026-11-22T10:14:03"), and a browser reads
    a zone-less string as *local* time — which put every "placed" and "paid"
    an hour or two out for anyone not on UTC. So a timestamp with no zone on
    it is read as the UTC it is; one that names its zone is taken at its
    word. (Calendar dates — "2026-11-22" — are not moments and never come
    through here.) */
export const apiTime = (value) => {
  if (value instanceof Date) return value;
  const text = String(value);
  const zoned = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(text);
  return new Date(zoned || !text.includes("T") ? text : `${text}Z`);
};

export const formatDate = (value) => {
  if (!value) return "—";
  const d = value.length === 10 ? new Date(`${value}T12:00:00`) : apiTime(value);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
};

/** How long ago, the way a person says it — "today", "yesterday", "5 days
 *  ago", "3 weeks ago" — and the date itself from two months back. Days are
 *  counted on the calendar, not in 24-hour blocks. */
export const sinceLabel = (value, now = new Date()) => {
  if (!value) return "—";
  const then = value.length === 10 ? new Date(`${value}T12:00:00`) : apiTime(value);
  const startOf = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOf(now) - startOf(then)) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 14) return `${days} days ago`;
  if (days < 60) return `${Math.floor(days / 7)} weeks ago`;
  return formatDate(value);
};

export const formatDateTime = (value) => {
  if (!value) return "—";
  return apiTime(value).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
};

/** A timestamp as the desk reads it: real time and date, short. The clock
    always shows; only the label in front of it trades freshness words for
    calendar words ("Today, 3:45 PM" → "Tue, 3:45 PM" → "Nov 12, 3:45 PM"). */
export const stampTime = (value) => {
  if (!value) return "—";
  const d = apiTime(value);
  const now = new Date();
  const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  const sameDay = (a, b) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate();
  if (sameDay(d, now)) return `Today, ${time}`;
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  if (sameDay(d, yesterday)) return `Yesterday, ${time}`;
  if (now - d < 7 * 86_400_000) {
    return `${d.toLocaleDateString("en-US", { weekday: "short" })}, ${time}`;
  }
  const date = d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    ...(d.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
  return `${date}, ${time}`;
};

/** Local-timezone YYYY-MM-DD — toISOString() is UTC and drifts a day near midnight. */
export const isoDate = (date) => {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
};

export const todayISO = () => isoDate(new Date());

/** The most of one bake the server accepts on a single basket line
    (schemas.OrderItemRequest). The quantity pickers stop here, so a
    well-stocked shelf can never walk a basket into a refused checkout. */
export const MAX_LINE_QUANTITY = 99;

/** Letter shown on the plain fallback tile when a product has no photo. */
export const productInitial = (name) => (name ?? "?").trim().charAt(0).toUpperCase();

/* ------------------------------------------------------------------ */
/* Bulk (event & catering) enquiries                                   */
/* ------------------------------------------------------------------ */

export const BULK_STATUSES = [
  { value: "new", label: "New", badge: "badge-warning" },
  { value: "contacted", label: "Contacted", badge: "badge-info" },
  { value: "quoted", label: "Quoted", badge: "badge-accent" },
  { value: "confirmed", label: "Confirmed", badge: "badge-success" },
  { value: "declined", label: "Declined", badge: "badge-error" },
];

const BULK_MAP = Object.fromEntries(BULK_STATUSES.map((s) => [s.value, s]));
export const bulkLabel = (value) => BULK_MAP[value]?.label ?? value;
export const bulkBadge = (value) => BULK_MAP[value]?.badge ?? "badge-ghost";

export const EVENT_TYPES = [
  { value: "birthday", label: "Birthday" },
  { value: "wedding", label: "Wedding" },
  { value: "corporate", label: "Corporate / office" },
  { value: "holiday", label: "Holiday gathering" },
  { value: "other", label: "Something else" },
];

/* ------------------------------------------------------------------ */
/* Order domain                                                        */
/* ------------------------------------------------------------------ */

/* A box's journey, and the group each stop belongs to — the group is what
   its badge says at a glance (components/OrderStatusBadge.jsx draws it):
     action      waiting on the shop: Pending
     kitchen     in the kitchen: Confirmed, Baking, Ready
     on-its-way  Out for delivery
     done        Delivered
     stopped     Cancelled — the one that never left the shop */
export const ORDER_STATUSES = [
  { value: "pending", label: "Pending", group: "action" },
  { value: "confirmed", label: "Confirmed", group: "kitchen" },
  { value: "baking", label: "Baking", group: "kitchen" },
  { value: "ready", label: "Ready", group: "kitchen" },
  { value: "out_for_delivery", label: "Out for delivery", group: "on-its-way" },
  { value: "delivered", label: "Delivered", group: "done" },
  { value: "cancelled", label: "Cancelled", group: "stopped" },
];

const STATUS_MAP = Object.fromEntries(ORDER_STATUSES.map((s) => [s.value, s]));

/** A status as a person writes it — the wire format's underscores never
 *  reach a screen, even for a value this map doesn't know. */
export const statusLabel = (value) =>
  STATUS_MAP[value]?.label ??
  String(value ?? "")
    .replace(/_/g, " ")
    .replace(/^./, (c) => c.toUpperCase());

/** The group a status belongs to, or null for a value this map doesn't know. */
export const statusGroup = (value) => STATUS_MAP[value]?.group ?? null;

/** Index in the happy-path progression (cancelled breaks out of it). */
export const statusStep = (value) => ORDER_STATUSES.findIndex((s) => s.value === value);

export const PAYMENT_METHODS = {
  card: "Card",
  paypal: "PayPal",
  cash_on_delivery: "Cash on delivery",
};

/** The ways of paying that go through a provider's own page — the order is
    placed unpaid and becomes paid only on the provider's word. */
export const ONLINE_PAYMENT_METHODS = ["card", "paypal"];
export const paysOnline = (method) => ONLINE_PAYMENT_METHODS.includes(method);

/** The companies that actually move the money, as the API names them. */
export const PAYMENT_PROVIDERS = { stripe: "Stripe", paypal: "PayPal" };
export const providerLabel = (provider) =>
  PAYMENT_PROVIDERS[provider] ?? "the payment provider";

export const PAYMENT_STATUSES = {
  pending: { label: "Payment pending", badge: "badge-warning" },
  paid: { label: "Paid", badge: "badge-success" },
  refunded: { label: "Refunded", badge: "badge-neutral" },
  partially_refunded: { label: "Partially refunded", badge: "badge-warning" },
};

/* One row of the payment ledger — a single pay page and what became of it.
   The order's badge above is the four-word summary; these nine are the
   payment's own story, in the words the desk reads them. */
export const TRANSACTION_STATUSES = {
  pending: { label: "Awaiting payment", badge: "badge-warning" },
  processing: { label: "Settling", badge: "badge-info" },
  succeeded: { label: "Paid", badge: "badge-success" },
  failed: { label: "Failed", badge: "badge-error" },
  cancelled: { label: "Closed", badge: "badge-ghost" },
  expired: { label: "Expired", badge: "badge-ghost" },
  partially_refunded: { label: "Partially refunded", badge: "badge-warning" },
  refunded: { label: "Refunded", badge: "badge-neutral" },
  disputed: { label: "Disputed", badge: "badge-error" },
};

/* A refund is whatever the provider says it is — "sent" only on its word. */
export const REFUND_STATUSES = {
  succeeded: { label: "Sent back", badge: "badge-success" },
  pending: { label: "On its way", badge: "badge-info" },
  failed: { label: "Not sent", badge: "badge-error" },
};

/** Typed money ("12,50", "12.5", " 12 ") as a number of cents, or null when
    it isn't an amount. Whole cents only — "1.005" is refused, not rounded,
    because a refund must be exactly what the person meant. */
export const parseAmountToCents = (text) => {
  const cleaned = String(text ?? "").trim().replace(",", ".");
  if (!/^\d{1,7}(\.\d{1,2})?$/.test(cleaned)) return null;
  const [whole, fraction = ""] = cleaned.split(".");
  return Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
};

/** What a code takes off, in the words a shopper reads: "10% off" for a
    percent code, "€5.00 off" for a fixed-value voucher. Those vouchers carry
    percent_off 0 on the wire — printing "0% off" would deny the very saving
    the customer was handed. */
export const couponSaving = (coupon) =>
  coupon?.fixed_amount_off != null
    ? `${money(coupon.fixed_amount_off)} off`
    : `${coupon?.percent_off ?? 0}% off`;

/** "Tax (5%)" — the label comes from the same rule the server taxes by
    (applied to the discounted subtotal). With no approved rate (null while
    VAT treatment is pending review) it names the row plainly, exactly like
    the server's own Stripe page — and in practice the row is hidden while
    the quoted tax is zero, so no "Tax €0.00" ever reads as a 0%-VAT
    decision. VAT-inclusive figures (the German B2C mode) say "included"
    instead — the tax is inside the amounts, not added on top. The amount
    itself is never computed here: basket tallies come from
    POST /api/orders/quote. */
export const taxLabel = (included = false) => {
  if (included) return "VAT (included)";
  if (shopConfig.taxRate === null || shopConfig.taxRate === undefined) return "Tax";
  const percent = (shopConfig.taxRate * 100).toFixed(2).replace(/\.?0+$/, "");
  return `Tax (${percent}%)`;
};
