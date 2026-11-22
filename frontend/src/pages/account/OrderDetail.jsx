import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import {
  Bike,
  CheckCheck,
  CircleCheck,
  CircleX,
  Flame,
  Printer,
  ReceiptText,
  ShoppingBasket,
} from "lucide-react";
import client, { apiError } from "../../api/client";
import {
  formatDate,
  formatDateTime,
  money,
  ORDER_STATUSES,
  PAYMENT_METHODS,
  PAYMENT_STATUSES,
  paysOnline,
  providerLabel,
  statusBadge,
  statusLabel,
  statusStep,
} from "../../lib/format";
import { useCart } from "../../context/CartContext";
import { useToast } from "../../context/ToastContext";
import { Loading } from "../../components/Loading";

// Little pictogram per stop on the order's journey through the shop.
const STATUS_ICONS = {
  pending: ReceiptText,
  confirmed: CircleCheck,
  baking: Flame,
  ready: ShoppingBasket,
  out_for_delivery: Bike,
  delivered: CheckCheck,
  cancelled: CircleX,
};

export default function OrderDetail() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const toast = useToast();
  const { add } = useCart();
  const navigate = useNavigate();

  const [order, setOrder] = useState(null);
  const [error, setError] = useState(null);
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [reordering, setReordering] = useState(false);
  const [paying, setPaying] = useState(false);
  const [checking, setChecking] = useState(false);
  // Whether the provider has been asked at least once since this page
  // opened — before that, "we haven't received it" would be a guess.
  const [asked, setAsked] = useState(false);

  // Reconcile with the provider on demand — the button's word is true: it
  // asks Stripe or PayPal directly, so a webhook still in flight can't hold
  // the answer hostage. For PayPal this is also the moment the payment the
  // customer approved is actually taken. Idempotent and row-locked
  // server-side, so asking twice never charges twice.
  const checkPayment = useCallback(async () => {
    setChecking(true);
    try {
      const { data } = await client.post(`/payments/confirm/${id}`);
      setOrder(data);
    } catch {
      // The provider didn't answer — the honest "not received yet" line
      // stays, and the button stays for another go.
    } finally {
      setChecking(false);
      setAsked(true);
    }
  }, [id]);

  useEffect(() => {
    let retryTimer = null;
    client
      .get(`/orders/${id}`)
      .then(({ data }) => {
        setOrder(data);
        // Back from the provider's page with the payment in flight —
        // reconcile with the provider now so the page tells the truth
        // without waiting on the webhook. Coming back is not proof of
        // anything; the provider's answer is. (A cancelled order is asked
        // about too: money that landed on a pay page which outlived its
        // order is sent straight back, and this is what notices it.)
        if (
          params.get("placed") === "1" &&
          paysOnline(data.payment_method) &&
          data.payment_status === "pending"
        ) {
          checkPayment();
          // The provider's own bookkeeping can land a beat after the
          // redirect — one quiet retry catches it without the customer
          // lifting a finger.
          retryTimer = setTimeout(checkPayment, 4000);
        }
      })
      .catch((e) => setError(apiError(e, "Order not found")));
    return () => clearTimeout(retryTimer);
  }, [id, params, checkPayment]);

  if (error) {
    return (
      <div className="py-16 text-center">
        <p className="mt-3 opacity-70">{error}</p>
        <Link to="/account/orders" className="btn btn-primary btn-sm btn mt-4">All orders</Link>
      </div>
    );
  }
  if (!order) return <Loading label="Finding your order…" />;

  const step = statusStep(order.status);
  const cancelled = order.status === "cancelled";
  const online = paysOnline(order.payment_method);
  const provider = providerLabel(order.payment_provider);
  const unpaid = order.payment_status === "pending";
  // The customer has done their part and the provider is still settling
  // (a bank debit on its way, a payment PayPal is reviewing): nothing to
  // pay again, nothing to do but wait for the receipt.
  const settling = unpaid && order.payment_state === "processing";
  const leftPayPage =
    params.get("stripe") === "cancelled" || params.get("paypal") === "cancelled";
  const payment = PAYMENT_STATUSES[order.payment_status] ?? {
    label: order.payment_status,
    badge: "badge-ghost",
  };

  const cancelOrder = async () => {
    setCancelling(true);
    try {
      const { data } = await client.post(`/orders/${order.id}/cancel`);
      setOrder(data);
      toast.info(
        data.cancel_fee > 0
          ? `Order cancelled — a ${money(data.cancel_fee, data.currency)} late-cancellation fee was kept`
          : "Order cancelled — stock returned to the case"
      );
      setConfirmingCancel(false);
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setCancelling(false);
    }
  };

  // An online order that was placed but never paid gets its pay page back
  // (a fresh Stripe link, or the PayPal page it already has) — the customer
  // shouldn't have to rebuild the basket from scratch.
  const completePayment = async () => {
    if (paying) return;
    setPaying(true);
    try {
      const { data } = await client.post(`/payments/${order.id}/session`);
      if (data.payment_url) {
        window.location.assign(data.payment_url);
        return;
      }
      // No link came back: there is nothing (more) to pay — the payment
      // landed on the earlier page, or the provider is still settling one
      // already made. Show the order as it now stands, and say which.
      setOrder(data);
      if (data.payment_status === "paid") {
        toast.success("This order is already paid — nothing more to do");
      } else if (data.payment_state === "processing") {
        toast.info("Your payment is still being confirmed — nothing more to do");
      } else {
        toast.info("There is no payment to complete on this order right now");
      }
      setPaying(false);
    } catch (err) {
      toast.error(apiError(err, "Couldn't open the payment page — try again"));
      setPaying(false);
      // A refusal usually means the order moved on while this page sat
      // open (paid, cancelled, its window closed) — show its true state.
      client
        .get(`/orders/${order.id}`)
        .then(({ data }) => setOrder(data))
        .catch(() => {});
    }
  };

  const orderAgain = async () => {
    setReordering(true);
    try {
      const missing = [];
      for (const item of order.items) {
        if (!item.product_slug) {
          missing.push(item.product_name);
          continue;
        }
        try {
          const { data: product } = await client.get(`/products/${item.product_slug}`);
          // Sold out stays sold out — never sneak one into the basket.
          if (product.stock <= 0) {
            missing.push(item.product_name);
            continue;
          }
          add(product, Math.min(item.quantity, product.stock));
        } catch {
          missing.push(item.product_name);
        }
      }
      if (missing.length) {
        toast.info(
          missing.length === order.items.length
            ? "Those items are no longer in the case"
            : `Added what's available — ${missing.join(", ")} is no longer in the case`
        );
      } else {
        toast.success("Items added to your basket");
      }
      navigate("/cart");
    } finally {
      setReordering(false);
    }
  };

  return (
    <div>
      {params.get("placed") === "1" && !cancelled && (
        <div
          className={`alert animate-pop mb-6 shadow-md ${
            order.payment_status === "paid" || !online ? "alert-success" : "alert-info"
          }`}
          role="status"
        >
          <div>
            {/* "Confirmed" is a word for an order the shop can start on:
                paid, or cash at the door. Until an online payment lands,
                the order is placed — and says what it is waiting for. */}
            <p className="font-bold">
              {!online || order.payment_status === "paid"
                ? "Order confirmed"
                : settling
                  ? "Order placed — payment being confirmed"
                  : "Order placed — waiting for your payment"}
            </p>
            {online ? (
              order.payment_status === "paid" ? (
                <p className="text-sm">Paid — your receipt was emailed to you.</p>
              ) : order.payment_status !== "pending" ? (
                <p className="text-sm">{payment.label}.</p>
              ) : settling ? (
                <p className="text-sm">
                  {provider} is still confirming your payment — with some ways
                  of paying that takes a while. There is nothing more to do:
                  your receipt is emailed the moment it clears.
                </p>
              ) : (
                <div className="text-sm">
                  {asked ? (
                    <p>
                      {provider} hasn&rsquo;t confirmed a payment for this order
                      yet. If you just paid, give it a moment and check again —
                      otherwise you can complete the payment below.
                    </p>
                  ) : (
                    <p>Checking with {provider} that your payment arrived…</p>
                  )}
                  <button
                    type="button"
                    className="btn btn-outline btn-xs mt-2"
                    onClick={checkPayment}
                    disabled={checking}
                  >
                    {checking ? "Checking…" : "Check again"}
                  </button>
                </div>
              )
            ) : (
              <p className="text-sm">Pay the courier at the door — cash only, please.</p>
            )}
            {/* The banner's own ink, underlined — the brand burgundy sinks
                into the darker of the two banner colours. */}
            <Link
              to={`/account/orders/${order.id}/receipt`}
              className="link mt-1 inline-block text-sm font-semibold"
            >
              View receipt
            </Link>
          </div>
        </div>
      )}

      {leftPayPage && unpaid && !settling && !cancelled && (
        <div className="alert alert-warning mb-6" role="status">
          <div>
            <p className="font-bold">You left the payment page before paying</p>
            <p className="text-sm">
              Nothing was charged. The order is saved — complete the payment
              below, or it cancels itself and the stock goes back to the case.
            </p>
          </div>
        </div>
      )}

      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link to="/account/orders" className="link link-secondary text-xs font-semibold">
            All orders
          </Link>
          <h2 className="font-display mt-2 text-3xl font-bold">{order.order_number}</h2>
          <p className="text-sm opacity-60">Placed {formatDateTime(order.created_at)}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`badge badge-lg ${statusBadge(order.status)} border`}>
            {statusLabel(order.status)}
          </span>
          <span className={`badge badge-lg ${payment.badge} border`}>{payment.label}</span>
          <Link
            to={`/account/orders/${order.id}/receipt`}
            className="btn btn-outline btn-sm ml-1"
          >
            <Printer size={15} />
            Receipt
          </Link>
        </div>
      </div>

      {/* Progress */}
      {!cancelled && (
        <ul className="steps steps-vertical w-full sm:steps-horizontal">
          {ORDER_STATUSES.slice(0, 6).map((s) => (
            <li key={s.value} className={`step ${statusStep(s.value) <= step ? "step-secondary" : ""}`}>
              {s.label}
            </li>
          ))}
        </ul>
      )}
      {cancelled && (
        <div className="alert alert-error alert-soft mb-6">
          <span>
            This order was cancelled.{" "}
            {order.payment_status === "refunded" && "Your payment was refunded in full."}
            {order.payment_status === "partially_refunded" &&
              (order.cancel_fee > 0
                ? `Your payment was refunded less the ${money(order.cancel_fee, order.currency)} late-cancellation fee — the ovens had already started.`
                : `${money(order.refunded_amount, order.currency)} of your payment was refunded.`)}
            {/* Cancelled while the provider still holds the money: the
                refund has not been confirmed, so it isn't claimed. */}
            {online && order.payment_status === "paid" &&
              "Your refund hasn't been confirmed yet — if it doesn't reach you within a few days, please contact the bakery."}
          </span>
        </div>
      )}

      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-[1fr_360px]">
        <div className="space-y-6">
          {/* Items */}
          <section className="rounded-box glass p-5 shadow-sm ring-1 ring-base-300/60">
            <div className="mb-4 flex items-center justify-between gap-3">
              <h3 className="font-display text-lg font-semibold">In the box</h3>
              <button
                type="button"
                className="btn btn-ghost btn-xs"
                onClick={orderAgain}
                disabled={reordering}
              >
                {reordering ? "Adding…" : "Order again"}
              </button>
            </div>
            <ul className="divide-y divide-base-200">
              {order.items.map((item) => (
                <li key={item.id} className="flex items-center justify-between gap-3 py-3">
                  <div>
                    {item.product_slug ? (
                      <Link to={`/products/${item.product_slug}`} className="font-semibold hover:text-secondary">
                        {item.product_name}
                      </Link>
                    ) : (
                      <span className="font-semibold">{item.product_name}</span>
                    )}
                    <p className="text-xs opacity-60">
                      {money(item.unit_price, order.currency)} × {item.quantity}
                    </p>
                  </div>
                  <p className="font-bold">{money(item.subtotal, order.currency)}</p>
                </li>
              ))}
            </ul>
          </section>

          {/* Timeline */}
          <section className="rounded-box glass p-5 shadow-sm ring-1 ring-base-300/60">
            <h3 className="font-display mb-4 text-lg font-semibold">Bake log</h3>
            <ol className="relative space-y-4 border-l-2 border-dashed border-base-300 pl-6">
              {order.timeline.map((event) => {
                const Icon = STATUS_ICONS[event.status] ?? ReceiptText;
                return (
                  <li key={event.id} className="relative">
                    <span className="brand-gradient absolute top-0 -left-[34px] flex h-5 w-5 items-center justify-center rounded-full text-secondary-content">
                      <Icon size={12} />
                    </span>
                    <p className="font-semibold">{statusLabel(event.status)}</p>
                    <p className="text-xs opacity-60">
                      {formatDateTime(event.created_at)}
                      {event.comment && ` — ${event.comment}`}
                    </p>
                  </li>
                );
              })}
            </ol>
          </section>
        </div>

        {/* Side details */}
        <aside className="space-y-6">
          <section className="rounded-box bg-base-200/70 p-5">
            <h3 className="font-display mb-3 text-lg font-semibold">The tally</h3>
            <div className="space-y-1.5 text-sm">
              <div className="flex justify-between"><span className="opacity-70">Subtotal</span><span>{money(order.subtotal, order.currency)}</span></div>
              {order.discount_amount > 0 && (
                <div className="flex justify-between text-secondary">
                  <span>Discount ({order.coupon_code})</span>
                  <span>−{money(order.discount_amount, order.currency)}</span>
                </div>
              )}
              {/* No tax row while none was charged — the basket and checkout
                  hide it for the same reason: "Tax €0.00" would read as a
                  0%-VAT decision the shop hasn't made. */}
              {order.tax > 0 && (
                <div className="flex justify-between"><span className="opacity-70">{order.tax_included ? "VAT (included)" : "Tax"}</span><span>{money(order.tax, order.currency)}</span></div>
              )}
              <div className="flex justify-between">
                <span className="opacity-70">Delivery</span>
                <span>{order.shipping_fee === 0 ? <span className="font-semibold text-success">FREE</span> : money(order.shipping_fee, order.currency)}</span>
              </div>
              <div className="dash-divider flex justify-between pt-2 text-base font-bold">
                <span>Total</span><span>{money(order.total, order.currency)}</span>
              </div>
              {order.cancel_fee > 0 && (
                <div className="flex justify-between pt-1 text-warning">
                  <span>Late-cancellation fee kept</span>
                  <span>{money(order.cancel_fee, order.currency)}</span>
                </div>
              )}
              {order.refunded_amount > 0 && (
                <div className="flex justify-between pt-1">
                  <span className="opacity-70">Refunded to you</span>
                  <span>{money(order.refunded_amount, order.currency)}</span>
                </div>
              )}
            </div>
            {/* "Paid via" only once it is — an unpaid order names its method
                without claiming the money arrived. */}
            <p className="mt-3 text-xs opacity-60">
              {order.payment_status === "pending" ? "Payment by" : "Paid via"}{" "}
              {PAYMENT_METHODS[order.payment_method] ?? order.payment_method}
            </p>
          </section>

          <section className="rounded-box bg-base-200/70 p-5 text-sm">
            <h3 className="font-display mb-3 text-lg font-semibold">Delivering to</h3>
            <p className="font-semibold">{order.recipient}</p>
            {order.phone && <p className="opacity-70">{order.phone}</p>}
            <p className="mt-1 opacity-70">
              {order.line1}{order.line2 ? `, ${order.line2}` : ""}<br />
              {order.city}, {order.state} {order.postal_code}<br />
              {order.country}
            </p>
            {order.delivery_date && (
              <p className="mt-3">
                <span className="badge badge-accent text-accent-content">{formatDate(order.delivery_date)}</span>
              </p>
            )}
            {order.note && (
              <p className="mt-3 rounded-box bg-base-100/70 p-3 text-xs italic opacity-80">“{order.note}”</p>
            )}
          </section>

          {online && unpaid && !cancelled && settling && (
            <section className="rounded-box border border-dashed border-info/50 bg-info/5 p-5">
              <p className="text-sm font-semibold">Payment being confirmed</p>
              <p className="mt-1 text-xs opacity-70">
                You&rsquo;ve done your part — {provider} is still settling this
                payment. Your receipt is emailed the moment it clears, and
                there is nothing to pay again.
              </p>
              <button
                type="button"
                className="btn btn-outline btn-sm mt-3 w-full"
                onClick={checkPayment}
                disabled={checking}
              >
                {checking ? "Checking…" : "Check again"}
              </button>
            </section>
          )}

          {online && unpaid && !cancelled && !settling && (
            <section className="rounded-box border border-dashed border-secondary/50 bg-secondary/5 p-5">
              <p className="text-sm font-semibold">Payment not completed</p>
              <p className="mt-1 text-xs opacity-70">
                This order is saved but not paid yet. If it stays unpaid, it
                cancels itself automatically and the stock goes back.
              </p>
              <button
                type="button"
                className="btn btn-secondary btn-sm mt-3 w-full text-secondary-content"
                onClick={completePayment}
                disabled={paying}
              >
                {paying
                  ? order.payment_method === "paypal"
                    ? "Opening PayPal…"
                    : "Opening the payment page…"
                  : order.payment_method === "paypal"
                    ? "Complete payment with PayPal"
                    : "Complete payment"}
              </button>
            </section>
          )}

          {order.can_be_cancelled && (
            <section className="rounded-box border border-dashed border-base-300 p-5">
              {confirmingCancel ? (
                <div className="space-y-3">
                  <p className="text-sm font-semibold">Cancel this order?</p>
                  <p className="text-xs opacity-70">
                    {order.cancellation_fee > 0
                      ? `Baking has started — a ${money(order.cancellation_fee, order.currency)} late-cancellation fee stays with the shop${
                          order.payment_status === "paid" ? " and your refund is reduced by it" : ""
                        }.`
                      : `Stock goes back in the case${
                          order.payment_status === "paid"
                            ? " and your payment is refunded"
                            : order.payment_status === "partially_refunded"
                              ? " and the rest of your payment is refunded"
                              : ""
                        }.`}
                    {settling &&
                      " The payment still being confirmed is sent back to you as soon as it lands."}
                  </p>
                  <div className="flex gap-2">
                    <button type="button" className="btn btn-error btn-sm" onClick={cancelOrder} disabled={cancelling}>
                      {cancelling ? <span className="loading loading-spinner loading-xs" /> : "Yes, cancel it"}
                    </button>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => setConfirmingCancel(false)}>
                      Keep it
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  {order.cancellation_fee > 0 && (
                    <p className="mb-2 text-xs font-semibold text-warning">
                      Baking has started — cancelling now keeps a {money(order.cancellation_fee, order.currency)} fee with the shop.
                    </p>
                  )}
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm w-full text-error"
                    onClick={() => setConfirmingCancel(true)}
                  >
                    Cancel order
                  </button>
                </>
              )}
            </section>
          )}
        </aside>
      </div>
    </div>
  );
}
