import { ArrowLeft, Printer } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import client, { apiError } from "../../api/client";
import { Loading } from "../../components/Loading";
import {
  formatDate,
  formatDateTime,
  money,
  PAYMENT_METHODS,
  PAYMENT_STATUSES,
  paysOnline,
} from "../../lib/format";

/** What the receipt may say about the money — only ever what is true of
    this order right now. A receipt is the one page a customer keeps, so it
    never calls an unpaid online order "due at the door", or a refunded one
    "paid". Returns [the payment line, the closing line]. */
function paymentWords(order) {
  const online = paysOnline(order.payment_method);
  const cancelled = order.status === "cancelled";
  const refunded = money(order.refunded_amount, order.currency);
  const proof = "This receipt is your proof of purchase — keep it safe.";
  switch (order.payment_status) {
    case "paid":
      return cancelled && online
        ? ["Paid — the refund for this cancelled order has not been confirmed yet.", ""]
        : ["Paid in full — thank you.", proof];
    case "partially_refunded":
      return [
        order.refunded_amount > 0 ? `Paid — ${refunded} of it refunded.` : "Paid — partly refunded.",
        proof,
      ];
    case "refunded":
      return ["Refunded in full.", "This order was refunded — nothing is owed either way."];
    default:
      if (cancelled) {
        return [
          online ? "Order cancelled — nothing was charged." : "Order cancelled — nothing is due.",
          "",
        ];
      }
      if (online) {
        return order.payment_state === "processing"
          ? [
              "Payment being confirmed by the provider.",
              "Your receipt is emailed the moment the payment clears.",
            ]
          : [
              "Not paid yet — this page is not a proof of purchase.",
              "Your receipt is emailed once the payment is confirmed.",
            ];
      }
      return ["Due to the courier at your door.", "An order confirmation was emailed to you."];
  }
}

/** A customer's proof of purchase — on screen and on paper (Print / save
    PDF). Deliberately its own page, free of the account chrome, so what
    prints is the receipt and nothing else. */
export default function OrderReceipt() {
  const { id } = useParams();
  const [order, setOrder] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    client
      .get(`/orders/${id}`)
      .then(({ data }) => setOrder(data))
      .catch((e) => setError(apiError(e, "Order not found")));
  }, [id]);

  if (error) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-16 text-center">
        <p className="opacity-70">{error}</p>
        <Link to="/account/orders" className="btn btn-primary btn-sm btn mt-4">
          All orders
        </Link>
      </div>
    );
  }
  if (!order) return <Loading label="Printing your receipt…" />;

  const payment = PAYMENT_STATUSES[order.payment_status] ?? {
    label: order.payment_status,
    badge: "badge-ghost",
  };
  const [paymentLine, closingLine] = paymentWords(order);

  return (
    <div className="mx-auto max-w-2xl px-4 py-10">
      {/* Screen-only controls */}
      <div className="no-print mb-4 flex items-center justify-between">
        <Link
          to={`/account/orders/${order.id}`}
          className="btn btn-ghost btn-sm gap-1.5"
        >
          <ArrowLeft size={15} />
          Order
        </Link>
        <button
          type="button"
          className="btn btn-primary btn-sm gap-1.5"
          onClick={() => window.print()}
        >
          <Printer size={15} />
          Print / save PDF
        </button>
      </div>

      <article className="receipt-sheet rounded-box bg-base-100 p-8 shadow-sm ring-1 ring-base-300/60">
        {/* On paper the frame div becomes the receipt's inner rule; on
            screen it is invisible and changes nothing. */}
        <div className="receipt-frame">
        {/* Shop identity */}
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <img
              src="/static/site/logo.png"
              alt=""
              className="h-11 w-11 object-contain"
            />
            <div>
              <p className="font-display text-lg leading-tight font-bold">
                Djini&rsquo;s{" "}
                <span className="italic text-secondary">Bakehouse</span>
              </p>
              <p className="text-xs opacity-60">Receipt of purchase</p>
            </div>
          </div>
          <span className={`badge badge-lg border ${payment.badge}`}>
            {payment.label}
          </span>
        </div>

        <div className="mt-6 border-t-2 border-dashed border-base-300 pt-5">
          <p className="font-display text-2xl font-bold">{order.order_number}</p>
          <p className="mt-0.5 text-sm opacity-60">
            Placed {formatDateTime(order.created_at)}
            {order.delivery_date && ` · delivery ${formatDate(order.delivery_date)}`}
          </p>
        </div>

        {/* Line items */}
        <ul className="mt-5 border-t-2 border-dashed border-base-300 pt-3">
          {order.items.map((item) => (
            <li
              key={item.id}
              className="flex items-baseline justify-between gap-4 border-b border-dashed border-base-300 py-2.5 last:border-0"
            >
              <span className="font-semibold">
                {item.product_name}
                <span className="ml-2 text-sm font-normal opacity-60">
                  {money(item.unit_price, order.currency)} × {item.quantity}
                </span>
              </span>
              <span className="shrink-0 font-bold">{money(item.subtotal, order.currency)}</span>
            </li>
          ))}
        </ul>

        {/* Totals */}
        <div className="mt-2 space-y-1.5 text-sm">
          <div className="flex justify-between">
            <span className="opacity-70">Subtotal</span>
            <span>{money(order.subtotal, order.currency)}</span>
          </div>
          {order.discount_amount > 0 && (
            <div className="flex justify-between text-success">
              <span>Discount ({order.coupon_code})</span>
              <span>−{money(order.discount_amount, order.currency)}</span>
            </div>
          )}
          <div className="flex justify-between">
            <span className="opacity-70">{order.tax_included ? "VAT (included)" : "Tax"}</span>
            <span>{money(order.tax, order.currency)}</span>
          </div>
          <div className="flex justify-between">
            <span className="opacity-70">Delivery</span>
            <span>
              {order.shipping_fee === 0 ? (
                <span className="font-semibold text-success">FREE</span>
              ) : (
                money(order.shipping_fee, order.currency)
              )}
            </span>
          </div>
          <div className="dash-divider mt-2 flex justify-between border-t-2 pt-2.5 text-base font-bold">
            <span>Total</span>
            <span>{money(order.total, order.currency)}</span>
          </div>
          {order.cancel_fee > 0 && (
            <div className="flex justify-between pt-1 text-warning">
              <span>Late-cancellation fee kept</span>
              <span>{money(order.cancel_fee, order.currency)}</span>
            </div>
          )}
          {order.refunded_amount > 0 && (
            <div className="flex justify-between pt-1">
              <span className="opacity-70">Refunded</span>
              <span>{money(order.refunded_amount, order.currency)}</span>
            </div>
          )}
        </div>

        {/* Address + payment */}
        <div className="mt-6 grid gap-5 border-t-2 border-dashed border-base-300 pt-5 sm:grid-cols-2">
          <div className="text-sm">
            <p className="text-xs font-bold tracking-widest uppercase opacity-50">
              Delivered to
            </p>
            <p className="mt-1.5 font-semibold">{order.recipient}</p>
            <p className="opacity-70">
              {order.line1}
              {order.line2 ? `, ${order.line2}` : ""}
              <br />
              {order.city}, {order.state} {order.postal_code}
              <br />
              {order.country}
            </p>
          </div>
          <div className="text-sm">
            <p className="text-xs font-bold tracking-widest uppercase opacity-50">
              Payment
            </p>
            <p className="mt-1.5 font-semibold">
              {PAYMENT_METHODS[order.payment_method] ?? order.payment_method}
            </p>
            <p className="opacity-70">{paymentLine}</p>
          </div>
        </div>

        {order.note && (
          <p className="mt-5 rounded-box bg-base-200/60 p-3 text-xs italic opacity-80">
            &ldquo;{order.note}&rdquo;
          </p>
        )}

        <p className="mt-6 border-t-2 border-dashed border-base-300 pt-4 text-center text-xs opacity-60">
          {closingLine}
          {closingLine && " "}
          Thank you for baking with us.
        </p>
        </div>
      </article>
    </div>
  );
}
