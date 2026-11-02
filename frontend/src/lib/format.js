/** Shared formatting helpers and domain constants. */

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

export const money = (n) => usd.format(Number(n ?? 0));

export const formatDate = (value) => {
  if (!value) return "—";
  const d = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
};

export const formatDateTime = (value) => {
  if (!value) return "—";
  return new Date(value).toLocaleString("en-US", {
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
  const d = new Date(value);
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

/** "1440" -> "24 h", "2880" -> "2 days", "90" -> "1.5 h" */
export const formatPrepTime = (minutes) => {
  if (!minutes && minutes !== 0) return null;
  if (minutes < 60) return `${minutes} min`;
  const hours = minutes / 60;
  if (hours < 24) return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} h`;
  const days = hours / 24;
  return `${Number.isInteger(days) ? days : days.toFixed(1)} days`;
};

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

export const ORDER_STATUSES = [
  { value: "pending", label: "Pending", badge: "badge-warning" },
  { value: "confirmed", label: "Confirmed", badge: "badge-info" },
  { value: "baking", label: "Baking", badge: "badge-accent" },
  { value: "ready", label: "Ready", badge: "badge-accent" },
  { value: "out_for_delivery", label: "Out for delivery", badge: "badge-info" },
  { value: "delivered", label: "Delivered", badge: "badge-success" },
  { value: "cancelled", label: "Cancelled", badge: "badge-error" },
];

const STATUS_MAP = Object.fromEntries(ORDER_STATUSES.map((s) => [s.value, s]));

export const statusLabel = (value) => STATUS_MAP[value]?.label ?? value;
export const statusBadge = (value) => STATUS_MAP[value]?.badge ?? "badge-ghost";

/** Index in the happy-path progression (cancelled breaks out of it). */
export const statusStep = (value) => ORDER_STATUSES.findIndex((s) => s.value === value);

export const PAYMENT_METHODS = {
  card: "Card",
  cash_on_delivery: "Cash on delivery",
};
export const PAYMENT_STATUSES = {
  pending: { label: "Payment pending", badge: "badge-warning" },
  paid: { label: "Paid", badge: "badge-success" },
  refunded: { label: "Refunded", badge: "badge-neutral" },
  partially_refunded: { label: "Partially refunded", badge: "badge-warning" },
};

/** Default shop rules (mirrored & re-validated by the server at checkout). */
export const TAX_RATE = 0.0875;
export const SHIPPING_FEE = 5.99;
export const FREE_SHIPPING_THRESHOLD = 50;

/** Delivery charge for a basket — free once it clears the threshold. */
export const shippingFor = (subtotal) =>
  subtotal >= FREE_SHIPPING_THRESHOLD ? 0 : SHIPPING_FEE;

/* The checkout arithmetic, mirrored from the server (backend orders.py): every
   figure is rounded to cents as it's charged, tax falls on the discounted
   subtotal, and free delivery is judged on the pre-discount one. Every estimate
   the shop quotes — the basket tally, the checkout summary — runs through this
   one copy so the numbers a customer sees can never drift from the invoice. */
export const computeTotals = (subtotal, percentOff = 0) => {
  const cents = (n) => Math.round(n * 100) / 100;
  const discount = cents((subtotal * percentOff) / 100);
  const taxable = cents(subtotal - discount);
  const tax = cents(taxable * TAX_RATE);
  const shipping = shippingFor(subtotal);
  return {
    discount,
    taxable,
    tax,
    shipping,
    total: cents(taxable + tax + shipping),
  };
};

/** "Tax (8.75%)" — the label and the math always come from the same constant. */
export const taxLabel = () => {
  const percent = (TAX_RATE * 100).toFixed(2).replace(/\.?0+$/, "");
  return `Tax (${percent}%)`;
};
