import { Banknote, CreditCard, FlaskConical, Lock, Wallet } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import client, { apiError, checkoutPaused } from "../api/client";
import EmptyState from "../components/EmptyState";
import { Loading } from "../components/Loading";
import { useCart } from "../context/CartContext";
import { useToast } from "../context/ToastContext";
import useBasketQuote from "../hooks/useBasketQuote";
import { getStoredCoupon, storeCoupon } from "../lib/coupon";
import { deliversTo, describeArea } from "../lib/delivery";
import { fetchStore } from "../lib/store";
import {
  couponSaving,
  formatDate,
  isoDate,
  money,
  taxLabel,
  todayISO,
} from "../lib/format";
import { canSubmitWith } from "../lib/quote";

/** A numbered step heading — the two things left to do, in order. */
function StepHead({ n, title }) {
  return (
    <div className="flex items-center gap-3">
      <span className="brand-gradient flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-bold text-secondary-content">
        {n}
      </span>
      <h2 className="font-display text-xl font-semibold">{title}</h2>
    </div>
  );
}

/* The ways to pay, in the order they are offered. A rail appears only
   when /api/store says the shop can genuinely take it — a provider with no
   keys behind it is never a dead button. The card rail's own page decides
   which cards and wallets it shows this customer on this device, so the
   hint promises no particular one. */
const PAYMENT_OPTIONS = [
  {
    value: "card",
    Icon: CreditCard,
    title: "Card & online payment",
    hint: "On Stripe's secure page — your card, or another method it offers you",
    summary: "Paid on Stripe's secure page — card details never touch this shop",
    action: (total) => `Pay ${total}`,
    lock: "Card details are entered on Stripe's page, never here",
  },
  {
    value: "paypal",
    Icon: Wallet,
    title: "PayPal",
    hint: "Approve the payment in your PayPal account",
    summary: "You approve the payment on PayPal's own site, then come straight back",
    // The button that places the order says it costs money, and how much —
    // "continue" would undersell what pressing it commits the customer to.
    action: (total) => `Pay ${total} with PayPal`,
    lock: "You sign in on PayPal's site, never here",
  },
  {
    value: "cash_on_delivery",
    Icon: Banknote,
    title: "Cash on delivery",
    hint: "Pay the courier at your door",
    summary: "Cash on delivery — pay at the door",
    action: () => "Place order",
    lock: "Prices & stock re-checked before baking",
  },
];

/* One press of the pay button gets one name. If the request is sent twice —
   a double tap, a retry after the connection dropped mid-answer — the
   server recognises the name and hands back the order that press already
   placed instead of placing (and charging for) a second one. */
const newPressKey = () =>
  typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`;

const EMPTY_ADDRESS = {
  label: "Home",
  recipient: "",
  phone: "",
  line1: "",
  line2: "",
  city: "",
  state: "",
  postal_code: "",
  country: "",
  is_default: true,
};

/* key, label, required, placeholder, autocomplete token, input type, and
   the longest value the server takes (schemas.AddressBase). The autocomplete
   tokens let the browser fill a known address in one tap; the labels are
   written for any country's address, not one. */
const ADDRESS_FIELDS = [
  ["recipient", "Recipient name", true, "", "name", "text", 150],
  ["phone", "Phone", false, "", "tel", "tel", 30],
  ["line1", "Street address", true, "", "address-line1", "text", 255],
  ["line2", "Apartment, suite (optional)", false, "", "address-line2", "text", 255],
  ["city", "City", true, "", "address-level2", "text", 100],
  ["state", "State / region", true, "", "address-level1", "text", 100],
  ["postal_code", "Postal code", true, "", "postal-code", "text", 20],
  // No default country — nothing is filled in on the customer's behalf.
  ["country", "Country", true, "Germany", "country-name", "text", 100],
];

export default function Checkout() {
  const { items, clear } = useCart();
  const toast = useToast();
  const navigate = useNavigate();

  const [addresses, setAddresses] = useState(null);
  const [mode, setMode] = useState("saved"); // "saved" | "new"
  const [addressId, setAddressId] = useState(null);
  const [newAddress, setNewAddress] = useState(EMPTY_ADDRESS);
  const [deliveryDate, setDeliveryDate] = useState("");
  const [note, setNote] = useState("");
  const [placing, setPlacing] = useState(false);
  // Which rails are real on this shop (/api/store says so) — until an
  // online provider is configured, cash on delivery is the one honest option.
  const [rails, setRails] = useState({ card: false, paypal: false, rehearsing: {} });
  const [method, setMethod] = useState("cash_on_delivery");
  // Set when an online payment couldn't be STARTED (the provider didn't
  // answer) — the honest state: nothing charged, basket intact, and the
  // ways forward, right where the pay button is.
  const [cardError, setCardError] = useState(null);
  const pressKey = useRef(newPressKey());
  // Where the van goes (postal codes and areas from Settings; empty = no
  // limit). The server is the judge — this is the early word.
  const [deliveryArea, setDeliveryArea] = useState([]);

  const [couponInput, setCouponInput] = useState("");
  const [coupon, setCoupon] = useState(null); // { code, percent_off, description, min_subtotal }
  const [checkingCoupon, setCheckingCoupon] = useState(false);
  const [vouchers, setVouchers] = useState([]);

  // The server prices this basket (coupon included) — the summary shows its
  // figures, and the order button waits for them. Placement re-derives every
  // number independently server-side, so what's shown is never what
  // authorizes the charge.
  const quoteState = useBasketQuote(items, coupon?.code);

  // A refused quote with a code aboard almost always means the code itself
  // (expired, spent under this account, basket under its threshold) — drop
  // it with the reason and the summary re-prices. Anything still refused
  // without a code is the basket; the summary says what's wrong.
  useEffect(() => {
    if (quoteState.status === "error" && coupon) {
      setCoupon(null);
      setCouponInput("");
      storeCoupon("");
      toast.info(apiError(quoteState.error, "That code no longer applies — removed"));
    }
  }, [quoteState.status, quoteState.error, coupon]);

  const maxDate = useMemo(() => {
    const d = new Date();
    d.setDate(d.getDate() + 30);
    return isoDate(d);
  }, []);

  // Validate a code against the live coupon, silently if it was carried over
  // from the basket page (it may have stopped qualifying — the basket changed).
  const validateCoupon = async (code, { silent = false } = {}) => {
    try {
      const { data } = await client.get(
        `/orders/coupons/${encodeURIComponent(code)}`,
      );
      setCoupon(data);
      setCouponInput("");
      if (!silent) toast.success(`${data.code} applied — ${couponSaving(data)}`);
      return data;
    } catch (err) {
      setCoupon(null);
      if (!silent) toast.error(apiError(err, "That code didn't work"));
      return null;
    }
  };

  useEffect(() => {
    client
      .get("/auth/addresses")
      .then(({ data }) => {
        setAddresses(data);
        const preferred = data.find((a) => a.is_default) ?? data[0];
        if (preferred) {
          setAddressId(preferred.id);
          setMode("saved");
        } else {
          setMode("new");
        }
      })
      .catch(() => setAddresses([]));

    // A voucher tapped on the basket page rides along; if it no longer
    // applies (basket shrank below its threshold), it drops off quietly.
    const carried = getStoredCoupon();
    if (carried) validateCoupon(carried, { silent: true });
  }, []);

  // Quick-apply chips under the code box — every live voucher not yet applied
  useEffect(() => {
    client
      .get("/coupons")
      .then(({ data }) => setVouchers(data))
      .catch(() => setVouchers([]));
  }, []);

  // Which ways of paying does this shop really have? Card first when it
  // exists, then PayPal — cash stays the fallback that always works.
  useEffect(() => {
    fetchStore()
      .then((data) => {
        setDeliveryArea(data.delivery_postal_codes ?? []);
        const card = Boolean(data.card_payments);
        const paypal = Boolean(data.paypal_payments);
        setRails({
          card,
          paypal,
          // Per option: the two providers go live separately, and a live
          // card page must never wear the sandbox PayPal's notice.
          rehearsing: {
            card: Boolean(data.card_test_mode),
            paypal: Boolean(data.paypal_test_mode),
          },
        });
        if (card) setMethod("card");
        else if (paypal) setMethod("paypal");
      })
      .catch(() => setRails({ card: false, paypal: false, rehearsing: {} }));
  }, []);

  // A different basket, code, address, day or way of paying is a different
  // purchase — it gets a fresh name. The same purchase sent again keeps its
  // name, which is what lets the server recognise the repeat.
  const purchase = JSON.stringify([
    items.map((i) => [i.product_id, i.quantity]),
    coupon?.code ?? "",
    method,
    mode,
    addressId,
    mode === "new" ? newAddress : null,
    deliveryDate,
    note,
  ]);
  useEffect(() => {
    pressKey.current = newPressKey();
  }, [purchase]);

  if (addresses === null) return <Loading label="Fetching your address book…" />;

  if (items.length === 0) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-8">
        <EmptyState title="Nothing to check out" hint="Your basket is empty — fill it with something warm first.">
          <Link to="/products" className="btn btn-primary">Explore Our Bakes</Link>
        </EmptyState>
      </div>
    );
  }

  // The summary is the server's quote, not a local copy of its arithmetic.
  // While the first quote is in flight the line rows fall back to the
  // cached add-time prices (visual only), and no order can be placed.
  const quote = quoteState.quote;
  const quoteLines = new Map((quote?.items ?? []).map((i) => [i.product_id, i]));
  const submittable = canSubmitWith(quoteState);

  const setField = (key, value) => setNewAddress((a) => ({ ...a, [key]: value }));

  // Only the rails this shop can genuinely take (cash always can).
  const offered = PAYMENT_OPTIONS.filter(
    (option) => option.value === "cash_on_delivery" || rails[option.value],
  );
  const chosen = offered.find((option) => option.value === method) ?? offered.at(-1);

  // The postal code this order would go to, and whether the van reaches it.
  const chosenAddress = mode === "saved" ? addresses.find((a) => a.id === addressId) : null;
  const postalCode = (mode === "saved" ? chosenAddress?.postal_code : newAddress.postal_code) ?? "";
  const outsideArea =
    deliveryArea.length > 0 && postalCode.trim() !== "" && !deliversTo(postalCode, deliveryArea);

  const applyCoupon = async (codeArg) => {
    const code = (codeArg ?? couponInput).trim();
    if (!code) return;
    setCheckingCoupon(true);
    const applied = await validateCoupon(code);
    if (applied) storeCoupon(applied.code);
    setCheckingCoupon(false);
  };

  const placeOrder = async (e) => {
    e.preventDefault();
    if (placing) return; // the button disables, but Enter can still submit
    if (!submittable) return; // no settled quote for *this* basket — never charge stale numbers
    if (outsideArea) {
      toast.error(`Sorry — we don't deliver to ${postalCode.trim()} yet`);
      document.getElementById("delivery-area-note")?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    setPlacing(true);
    setCardError(null);
    try {
      const payload = {
        items: items.map((i) => ({ product_id: i.product_id, quantity: i.quantity })),
        payment_method: method,
      };
      if (mode === "saved" && addressId) payload.address_id = addressId;
      else payload.new_address = newAddress;
      if (deliveryDate) payload.delivery_date = deliveryDate;
      if (note.trim()) payload.note = note.trim();
      if (coupon) payload.coupon_code = coupon.code;

      const { data: order } = await client.post("/orders", payload, {
        headers: { "Idempotency-Key": pressKey.current },
      });
      clear();
      storeCoupon(""); // the code was spent with the order
      if (order.payment_url) {
        // Online orders finish on the provider's own page (Stripe's or
        // PayPal's) — payment details never touch this site. Back here
        // afterwards, the order page confirms with the provider itself.
        window.location.assign(order.payment_url);
        return;
      }
      toast.success(`Order ${order.order_number} confirmed`);
      navigate(`/account/orders/${order.id}?placed=1`, { replace: true });
    } catch (err) {
      toast.error(apiError(err, "Couldn't place the order"));
      // The server answered, and the answer was no: that press is over, so
      // trying again is a new one. (No answer at all keeps the name — the
      // order may exist, and the retry must find it rather than repeat it.)
      if (err?.response) pressKey.current = newPressKey();
      const status = err?.response?.status;
      // A 4xx is the shop saying no to something the customer can change —
      // the address, the day, the code, a line of the basket, the pace.
      // The toast has said which; no payment was attempted, so the
      // "payment didn't start" panel would only point at the wrong thing.
      const refused = status >= 400 && status < 500;
      if (method !== "cash_on_delivery" && !refused) {
        // The provider didn't answer (or the shop didn't): nothing was
        // charged and the order was rolled back server-side — say that
        // plainly at the button, with the ways forward.
        setCardError(
          apiError(err, "The payment couldn't be started — nothing was charged."),
        );
      }
      // A basket line the shelf can't cover is mended on the basket page
      // (the server names the line); everything else is fixed right here.
      if (err?.response?.headers?.["x-basket-product"]) navigate("/cart");
    } finally {
      setPlacing(false);
    }
  };

  return (
    <div className="mx-auto max-w-7xl px-4 py-10">
      <h1 className="font-display text-4xl font-semibold">Checkout</h1>
      <p className="mt-1 opacity-70">Almost there — the oven is preheating.</p>

      <form onSubmit={placeOrder} className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-[1fr_400px]">
        <div className="space-y-8">
          {/* Delivery address */}
          <section className="rounded-box glass p-6 shadow-sm ring-1 ring-base-300/60">
            <StepHead n={1} title="Delivery details" />

            {addresses.length > 0 && (
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                {addresses.map((a) => (
                  <label
                    key={a.id}
                    className={`cursor-pointer rounded-xl border-2 p-4 transition ${
                      mode === "saved" && addressId === a.id
                        ? "border-secondary bg-secondary/10"
                        : "border-base-300 hover:border-base-content/30"
                    }`}
                  >
                    <input
                      type="radio"
                      name="address"
                      className="radio radio-secondary radio-sm"
                      checked={mode === "saved" && addressId === a.id}
                      onChange={() => {
                        setMode("saved");
                        setAddressId(a.id);
                      }}
                    />
                    <span className="ml-2 align-middle font-semibold">{a.label}</span>
                    {a.is_default && <span className="badge badge-secondary badge-xs ml-2">default</span>}
                    <p className="mt-2 text-sm opacity-75">
                      {a.recipient}<br />
                      {a.line1}{a.line2 ? `, ${a.line2}` : ""}<br />
                      {a.city}, {a.state} {a.postal_code}
                    </p>
                  </label>
                ))}
                <label
                  className={`flex cursor-pointer items-center justify-center rounded-xl border-2 border-dashed p-4 text-sm font-semibold transition ${
                    mode === "new" ? "border-secondary bg-secondary/10 text-secondary" : "border-base-300 opacity-70"
                  }`}
                >
                  <input
                    type="radio"
                    name="address"
                    className="radio radio-secondary radio-sm"
                    checked={mode === "new"}
                    onChange={() => setMode("new")}
                  />
                  <span className="ml-2">New address</span>
                </label>
              </div>
            )}

            {(mode === "new" || addresses.length === 0) && (
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <div className="sm:col-span-2">
                  <label className="label text-xs" htmlFor="address-label">Label</label>
                  <input
                    id="address-label"
                    className="input input-bordered w-full"
                    value={newAddress.label}
                    onChange={(e) => setField("label", e.target.value)}
                    placeholder="Home, Work…"
                    maxLength={50}
                  />
                </div>
                {ADDRESS_FIELDS.map(([key, label, required, placeholder, autoComplete, type, maxLength]) => (
                  <div key={key} className={key === "line1" ? "sm:col-span-2" : ""}>
                    {/* Tied to its input by id — a screen reader announces
                        the field, and a tap on the label focuses it. */}
                    <label className="label text-xs" htmlFor={`address-${key}`}>{label}</label>
                    <input
                      id={`address-${key}`}
                      type={type}
                      className="input input-bordered w-full"
                      required={required}
                      placeholder={placeholder}
                      autoComplete={autoComplete}
                      maxLength={maxLength}
                      value={newAddress[key] ?? ""}
                      onChange={(e) => setField(key, e.target.value)}
                    />
                  </div>
                ))}
                <label className="label sm:col-span-2 cursor-pointer justify-start gap-3">
                  <input
                    type="checkbox"
                    className="toggle toggle-secondary toggle-sm"
                    checked={newAddress.is_default}
                    onChange={(e) => setField("is_default", e.target.checked)}
                  />
                  <span className="label-text text-sm">Save as my default address</span>
                </label>
              </div>
            )}

            {deliveryArea.length > 0 && (
              <p
                id="delivery-area-note"
                className={`mt-4 rounded-lg px-3 py-2 text-xs ${
                  outsideArea ? "bg-error/10 font-semibold text-error" : "bg-base-200/70 opacity-80"
                }`}
                role={outsideArea ? "alert" : undefined}
              >
                {outsideArea
                  ? `Sorry — we don't deliver to ${postalCode.trim()} yet. `
                  : ""}
                We deliver to postal codes {describeArea(deliveryArea)}.
              </p>
            )}
          </section>

          {/* Delivery + payment */}
          <section className="rounded-box glass p-6 shadow-sm ring-1 ring-base-300/60">
            <StepHead n={2} title="Delivery day & payment" />

            <div className="mt-5 max-w-xs">
              <label className="label mb-1.5 text-xs font-semibold" htmlFor="delivery-date">
                Delivery date (optional)
              </label>
              <input
                id="delivery-date"
                type="date"
                className="input input-bordered w-full"
                min={todayISO()}
                max={maxDate}
                value={deliveryDate}
                onChange={(e) => setDeliveryDate(e.target.value)}
              />
              <p className="mt-1.5 text-xs opacity-60">Leave blank for the next delivery run.</p>
            </div>

            <fieldset className="mt-5">
              <legend className="label mb-1.5 text-xs font-semibold">How would you like to pay?</legend>
              <div className="grid gap-2 sm:grid-cols-2">
                {offered.map(({ value, Icon, title, hint }) => (
                  <label
                    key={value}
                    className={`flex cursor-pointer items-center gap-3 rounded-xl border-2 p-3.5 transition focus-within:ring-2 focus-within:ring-secondary/40 ${
                      method === value
                        ? "border-secondary bg-secondary/10"
                        : "border-base-300 hover:border-base-content/30"
                    }`}
                  >
                    <input
                      type="radio"
                      name="payment"
                      className="radio radio-secondary radio-sm"
                      checked={method === value}
                      onChange={() => {
                        setMethod(value);
                        setCardError(null);
                      }}
                    />
                    <Icon size={20} className="shrink-0 text-secondary" aria-hidden="true" />
                    <div className="min-w-0">
                      <p className="text-sm font-semibold">{title}</p>
                      <p className="text-xs opacity-60">{hint}</p>
                    </div>
                  </label>
                ))}
              </div>
              {/* A rehearsal says so, on the option it is true of: on test
                  keys a real card is declined, and nobody should be left
                  wondering why. */}
              {rails.rehearsing[method] && (
                <p className="mt-2 flex items-start gap-2 rounded-lg bg-warning/15 px-3 py-2 text-xs" role="note">
                  <FlaskConical size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
                  <span>
                    {method === "paypal" ? (
                      <>
                        PayPal is in <strong>test mode</strong> on this shop right now — no real
                        money is taken, and only PayPal sandbox accounts can pay.
                      </>
                    ) : (
                      <>
                        Card payments are in <strong>test mode</strong> on this shop right now — no
                        real money is taken and real cards are declined.
                      </>
                    )}{" "}
                    Cash on delivery works as usual.
                  </span>
                </p>
              )}
            </fieldset>

            <div className="mt-5">
              <label className="label mb-1.5 text-xs font-semibold" htmlFor="order-note">
                Note for the bakery (optional)
              </label>
              <textarea
                id="order-note"
                className="textarea textarea-bordered w-full"
                rows={2}
                maxLength={1000}
                placeholder="Gate code, cake message, leave-with-neighbor…"
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
            </div>
          </section>
        </div>

        {/* Summary */}
        <aside className="h-fit rounded-box bg-base-200/70 p-6 shadow-sm lg:sticky lg:top-24">
          <h2 className="font-display text-xl font-semibold">
            Your order
            {quoteState.status === "loading" && quote && (
              <span className="loading loading-spinner loading-xs ml-2 align-middle opacity-50" aria-label="Refreshing your total" />
            )}
          </h2>
          <ul className="mt-4 space-y-2 text-sm">
            {items.map((i) => (
              <li key={i.product_id} className="flex justify-between gap-3">
                <span className="opacity-80">
                  {i.name} <span className="opacity-60">× {i.quantity}</span>
                </span>
                <span className="shrink-0 font-semibold">
                  {money(quoteLines.get(i.product_id)?.line_total ?? i.unit_price * i.quantity)}
                </span>
              </li>
            ))}
          </ul>

          {/* Coupon */}
          <div className="dash-divider mt-4 space-y-2.5 pt-4">
            {coupon ? (
              <div className="flex items-center justify-between gap-2 text-sm">
                <span>
                  <span className="badge badge-secondary badge-sm font-bold">{coupon.code}</span>{" "}
                  <span className="opacity-70">{couponSaving(coupon)}</span>
                </span>
                <button
                  type="button"
                  className="btn btn-ghost btn-xs"
                  onClick={() => {
                    setCoupon(null);
                    setCouponInput("");
                    storeCoupon("");
                  }}
                >
                  Remove
                </button>
              </div>
            ) : (
              <>
                <div className="flex gap-2">
                  <input
                    className="input input-bordered input-sm w-full uppercase"
                    placeholder="Promo code"
                    value={couponInput}
                    onChange={(e) => setCouponInput(e.target.value.toUpperCase())}
                    aria-label="Promo code"
                  />
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm text-secondary-content"
                    onClick={() => applyCoupon()}
                    disabled={checkingCoupon || !couponInput.trim()}
                  >
                    {checkingCoupon ? "Checking…" : "Apply"}
                  </button>
                </div>
                {vouchers
                  .filter((v) => v.code !== coupon?.code)
                  .map((v) => (
                    <button
                      key={v.code}
                      type="button"
                      onClick={() => applyCoupon(v.code)}
                      className="btn btn-ghost btn-xs w-full justify-between border border-dashed border-base-300 px-3 text-xs hover:bg-base-200"
                    >
                      <span className="font-bold">{v.code} · {couponSaving(v)}</span>
                      <span className="opacity-70">
                        {v.min_subtotal
                          ? `boxes of ${money(v.min_subtotal)}+`
                          : "any basket"}
                      </span>
                    </button>
                  ))}
              </>
            )}
          </div>

          {quote ? (
            <div className="mt-4 space-y-2 text-sm">
              <div className="flex justify-between"><span className="opacity-70">Subtotal</span><span>{money(quote.subtotal)}</span></div>
              {quote.discount_amount > 0 && (
                <div className="flex justify-between text-secondary">
                  <span>Discount ({quote.coupon_code})</span><span>−{money(quote.discount_amount)}</span>
                </div>
              )}
              {/* No tax row while none is charged — a "Tax €0.00" line would
                  read as a 0%-VAT decision the shop hasn't made. */}
              {quote.tax > 0 && (
                <div className="flex justify-between"><span className="opacity-70">{taxLabel(quote.tax_included)}</span><span>{money(quote.tax)}</span></div>
              )}
              <div className="flex justify-between">
                <span className="opacity-70">Delivery</span>
                <span>{quote.shipping_fee === 0 ? <span className="font-semibold text-success">FREE</span> : money(quote.shipping_fee)}</span>
              </div>
              <div className="dash-divider flex justify-between pt-3 text-lg font-bold">
                <span>Total</span><span>{money(quote.total)}</span>
              </div>
              <div className="mt-3 space-y-1 text-xs opacity-70">
                {deliveryDate && <p>Delivery on {formatDate(deliveryDate)}</p>}
                <p>{chosen.summary}</p>
              </div>
            </div>
          ) : quoteState.status === "error" ? (
            <div className="mt-4">
              <div className="alert alert-warning text-sm" role="alert">
                <span>
                  {apiError(quoteState.error, "This basket can't be priced right now")}
                  {/* A pause is the shop's own doing — its message already
                      says what's wrong; basket-checking advice would not. */}
                  {!checkoutPaused(quoteState.error) && " — check your basket and try again."}
                </span>
              </div>
              {/* The basket page is where a line can be fixed or removed. */}
              {!checkoutPaused(quoteState.error) && (
                <Link to="/cart" className="btn btn-outline btn-sm mt-3 w-full">
                  Back to your basket
                </Link>
              )}
            </div>
          ) : (
            <div className="mt-4 space-y-2" aria-label="Pricing your basket">
              <div className="skeleton h-4 w-full" />
              <div className="skeleton h-4 w-2/3" />
              <div className="skeleton h-6 w-1/2" />
            </div>
          )}
          {/* A card payment that couldn't start — honest words and two ways
              forward, not a dead end. */}
          {cardError && method !== "cash_on_delivery" && (
            <div className="alert alert-warning mt-5 text-sm" role="alert">
              <span>
                <p className="font-semibold">The payment didn&rsquo;t start</p>
                <p className="opacity-80">
                  {cardError} Your basket is exactly as you left it — try
                  again, or pay cash at the door.
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="btn btn-secondary btn-xs text-secondary-content"
                    onClick={() => setCardError(null)}
                  >
                    Try again
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-xs"
                    onClick={() => {
                      setMethod("cash_on_delivery");
                      setCardError(null);
                    }}
                  >
                    Pay cash on delivery
                  </button>
                </div>
              </span>
            </div>
          )}
          <button type="submit" className="btn btn-secondary btn-lg mt-6 w-full text-secondary-content" disabled={placing || !submittable}>
            {placing || !submittable ? (
              <span className="loading loading-spinner loading-sm" aria-label="Working" />
            ) : (
              chosen.action(money(quote.total))
            )}
          </button>
          <p className="mt-3 flex items-center justify-center gap-1.5 text-center text-xs opacity-60">
            <Lock size={16} className="shrink-0" aria-hidden="true" />
            {chosen.lock}
          </p>
          {/* What pressing the button agrees to, with each document one tap
              away (new tabs — a half-filled checkout is not thrown away). */}
          <p className="mt-3 text-center text-xs opacity-60">
            By ordering you accept our{" "}
            <Link to="/terms" target="_blank" rel="noopener" className="link">terms</Link> and{" "}
            <Link to="/refunds" target="_blank" rel="noopener" className="link">
              refunds &amp; cancellations
            </Link>
            . See also the{" "}
            <Link to="/privacy" target="_blank" rel="noopener" className="link">privacy policy</Link>.
          </p>
        </aside>
      </form>
    </div>
  );
}
