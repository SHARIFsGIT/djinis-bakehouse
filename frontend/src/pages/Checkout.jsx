import { Banknote, CreditCard, Lock } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";
import client, { apiError, checkoutPaused } from "../api/client";
import EmptyState from "../components/EmptyState";
import { Loading } from "../components/Loading";
import { useCart } from "../context/CartContext";
import { useToast } from "../context/ToastContext";
import useBasketQuote from "../hooks/useBasketQuote";
import { getStoredCoupon, storeCoupon } from "../lib/coupon";
import {
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

const ADDRESS_FIELDS = [
  ["recipient", "Recipient name", true, ""],
  ["phone", "Phone", false, ""],
  ["line1", "Street address", true, ""],
  ["line2", "Apartment, suite (optional)", false, ""],
  ["city", "City", true, ""],
  ["state", "State", true, ""],
  ["postal_code", "ZIP / postal code", true, ""],
  // No default country — nothing is filled in on the customer's behalf.
  ["country", "Country", true, "Germany"],
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
  // Card appears only when the shop has Stripe configured (/api/store says
  // so) — until then cash on delivery is the one honest option.
  const [cardAvailable, setCardAvailable] = useState(false);
  const [method, setMethod] = useState("cash_on_delivery");

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
      if (!silent) toast.success(`${data.code} applied — ${data.percent_off}% off`);
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

  // Does this shop take cards? (Stripe configured = yes)
  useEffect(() => {
    client
      .get("/store")
      .then(({ data }) => {
        setCardAvailable(Boolean(data.card_payments));
        if (data.card_payments) setMethod("card");
      })
      .catch(() => setCardAvailable(false));
  }, []);

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
    setPlacing(true);
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

      const { data: order } = await client.post("/orders", payload);
      clear();
      storeCoupon(""); // the code was spent with the order
      if (order.payment_url) {
        // Card orders finish on Stripe's hosted page — card details never
        // touch this site. Back here afterwards, the order page confirms.
        window.location.assign(order.payment_url);
        return;
      }
      toast.success(`Order ${order.order_number} confirmed`);
      navigate(`/account/orders/${order.id}?placed=1`, { replace: true });
    } catch (err) {
      toast.error(apiError(err, "Couldn't place the order"));
      if (err?.response?.status === 400) navigate("/cart");
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
                  <label className="label text-xs">Label</label>
                  <input
                    className="input input-bordered w-full"
                    value={newAddress.label}
                    onChange={(e) => setField("label", e.target.value)}
                    placeholder="Home, Work…"
                    maxLength={50}
                  />
                </div>
                {ADDRESS_FIELDS.map(([key, label, required, placeholder]) => (
                  <div key={key} className={key === "line1" ? "sm:col-span-2" : ""}>
                    <label className="label text-xs">{label}</label>
                    <input
                      className="input input-bordered w-full"
                      required={required}
                      placeholder={placeholder}
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

            <div className="mt-5">
              <p className="label mb-1.5 text-xs font-semibold">Payment</p>
              <div className="grid gap-2 sm:grid-cols-2">
                {cardAvailable && (
                  <label
                    className={`flex cursor-pointer items-center gap-3 rounded-xl border-2 p-3.5 transition ${
                      method === "card"
                        ? "border-secondary bg-secondary/10"
                        : "border-base-300 hover:border-base-content/30"
                    }`}
                  >
                    <input
                      type="radio"
                      name="payment"
                      className="radio radio-secondary radio-sm"
                      checked={method === "card"}
                      onChange={() => setMethod("card")}
                    />
                    <CreditCard size={20} className="shrink-0 text-secondary" />
                    <div className="min-w-0">
                      <p className="text-sm font-semibold">Card</p>
                      <p className="text-xs opacity-60">Secure payment on Stripe's page</p>
                    </div>
                  </label>
                )}
                <label
                  className={`flex cursor-pointer items-center gap-3 rounded-xl border-2 p-3.5 transition ${
                    method === "cash_on_delivery"
                      ? "border-secondary bg-secondary/10"
                      : "border-base-300 hover:border-base-content/30"
                  }`}
                >
                  <input
                    type="radio"
                    name="payment"
                    className="radio radio-secondary radio-sm"
                    checked={method === "cash_on_delivery"}
                    onChange={() => setMethod("cash_on_delivery")}
                  />
                  <Banknote size={20} className="shrink-0 text-secondary" />
                  <div className="min-w-0">
                    <p className="text-sm font-semibold">Cash on delivery</p>
                    <p className="text-xs opacity-60">Pay the courier at your door</p>
                  </div>
                </label>
              </div>
            </div>

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
                  <span className="opacity-70">{coupon.percent_off}% off</span>
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
                      <span className="font-bold">{v.code} · {v.percent_off}% off</span>
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
                <p>
                  {method === "card"
                    ? "Paid by card on Stripe's secure page"
                    : "Cash on delivery — pay at the door"}
                </p>
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
            </div>
          ) : (
            <div className="mt-4 space-y-2" aria-label="Pricing your basket">
              <div className="skeleton h-4 w-full" />
              <div className="skeleton h-4 w-2/3" />
              <div className="skeleton h-6 w-1/2" />
            </div>
          )}
          <button type="submit" className="btn btn-secondary btn-lg mt-6 w-full text-secondary-content" disabled={placing || !submittable}>
            {placing || !submittable ? (
              <span className="loading loading-spinner loading-sm" />
            ) : method === "card" ? (
              `Pay ${money(quote.total)}`
            ) : (
              "Place order"
            )}
          </button>
          <p className="mt-3 flex items-center justify-center gap-1.5 text-center text-xs opacity-60">
            <Lock size={16} className="shrink-0" />
            {method === "card"
              ? "Card details are entered on Stripe's page, never here"
              : "Prices & stock re-checked before baking"}
          </p>
        </aside>
      </form>
    </div>
  );
}
