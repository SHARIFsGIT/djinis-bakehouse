import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import {
  Bike,
  CheckCheck,
  CircleCheck,
  CircleX,
  Flame,
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

  useEffect(() => {
    client
      .get(`/orders/${id}`)
      .then(({ data }) => setOrder(data))
      .catch((e) => setError(apiError(e, "Order not found")));
  }, [id]);

  if (error) {
    return (
      <div className="py-16 text-center">
        <p className="mt-3 opacity-70">{error}</p>
        <Link to="/account/orders" className="btn btn-primary btn-sm btn mt-4">Back to orders</Link>
      </div>
    );
  }
  if (!order) return <Loading label="Finding your order…" />;

  const step = statusStep(order.status);
  const cancelled = order.status === "cancelled";
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
          ? `Order cancelled — a ${money(data.cancel_fee)} late-cancellation fee was kept`
          : "Order cancelled — stock returned to the case"
      );
      setConfirmingCancel(false);
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setCancelling(false);
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
          add(product, Math.min(item.quantity, Math.max(product.stock, 1)));
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
      {params.get("placed") === "1" && (
        <div className="alert alert-success animate-pop mb-6 shadow-md">
          <div>
            <p className="font-bold">Order confirmed</p>
            <p className="text-sm">
              {order.payment_method === "card"
                ? "Your card was charged (simulated) — see you on delivery day."
                : "Pay the courier at the door — cash only, please."}
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
              `Your payment was refunded less the ${money(order.cancel_fee)} late-cancellation fee — the ovens had already started.`}
          </span>
        </div>
      )}

      <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_360px]">
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
                      {money(item.unit_price)} × {item.quantity}
                    </p>
                  </div>
                  <p className="font-bold">{money(item.subtotal)}</p>
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
              <div className="flex justify-between"><span className="opacity-70">Subtotal</span><span>{money(order.subtotal)}</span></div>
              {order.discount_amount > 0 && (
                <div className="flex justify-between text-secondary">
                  <span>Discount ({order.coupon_code})</span>
                  <span>−{money(order.discount_amount)}</span>
                </div>
              )}
              <div className="flex justify-between"><span className="opacity-70">Tax</span><span>{money(order.tax)}</span></div>
              <div className="flex justify-between">
                <span className="opacity-70">Delivery</span>
                <span>{order.shipping_fee === 0 ? <span className="font-semibold text-success">FREE</span> : money(order.shipping_fee)}</span>
              </div>
              <div className="dash-divider flex justify-between pt-2 text-base font-bold">
                <span>Total</span><span>{money(order.total)}</span>
              </div>
              {order.cancel_fee > 0 && (
                <div className="flex justify-between pt-1 text-warning">
                  <span>Late-cancellation fee kept</span>
                  <span>{money(order.cancel_fee)}</span>
                </div>
              )}
            </div>
            <p className="mt-3 text-xs opacity-60">Paid via {PAYMENT_METHODS[order.payment_method] ?? order.payment_method}</p>
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

          {order.can_be_cancelled && (
            <section className="rounded-box border border-dashed border-base-300 p-5">
              {confirmingCancel ? (
                <div className="space-y-3">
                  <p className="text-sm font-semibold">Cancel this order?</p>
                  <p className="text-xs opacity-70">
                    {order.cancellation_fee > 0
                      ? `Baking has started — a ${money(order.cancellation_fee)} late-cancellation fee stays with the shop${
                          order.payment_status === "paid" ? " and your refund is reduced by it" : ""
                        }.`
                      : `Stock goes back in the case${
                          order.payment_status === "paid" ? " and your payment is refunded" : ""
                        }.`}
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
                      Baking has started — cancelling now keeps a {money(order.cancellation_fee)} fee with the shop.
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
