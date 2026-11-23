import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { CalendarHeart, ChevronRight, Mail, Send, Trash2, Users } from "lucide-react";
import client, { apiError } from "../../api/client";
import {
  BULK_STATUSES,
  EVENT_TYPES,
  bulkBadge,
  bulkLabel,
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
} from "../../lib/format";
import { useToast } from "../../context/ToastContext";
import ConfirmDialog from "../../components/ConfirmDialog";
import { LoadError, Loading } from "../../components/Loading";
import Pager from "../../components/Pager";
import { useEscape, useFocusHandoff, useScrollLock } from "../../components/Drawer";
import { PaymentsPanel, providerHoldsMoney, ReconciliationReport } from "./OrderPayments";

/** The frame both rails open their drawer in — a dialog to assistive tech,
    closed by Escape or a click on the scrim, with the keyboard handed over
    on open and handed back on close. */
function DrawerFrame({ label, onClose, children }) {
  useEscape(onClose);
  const panel = useFocusHandoff();
  return (
    <div className="fixed inset-0 z-[70] flex justify-end" role="dialog" aria-modal="true" aria-label={label}>
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        ref={panel}
        tabIndex={-1}
        className="animate-pop relative h-full w-full max-w-md overflow-hidden bg-base-100 shadow-2xl outline-none"
      >
        {children}
      </div>
    </div>
  );
}

/** Drawer with the full order + status/payment controls. */
function OrderDrawer({ orderId, onClose, onChanged }) {
  const toast = useToast();
  useScrollLock();
  const [order, setOrder] = useState(null);
  const [failed, setFailed] = useState(null);
  const [status, setStatus] = useState("");
  const [comment, setComment] = useState("");
  // Whether this dial is mailbox news — the baker's call, per change.
  const [notify, setNotify] = useState(true);
  const [busy, setBusy] = useState(false);
  // Cancelling is the one dial that can't be turned back (and on a paid
  // card order it sends money) — it asks first.
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  // A payment relabel waiting for a yes: "paid" | "refunded" | null.
  const [confirmingPayment, setConfirmingPayment] = useState(null);
  const [paymentBusy, setPaymentBusy] = useState(false);
  // The provider's side of an online order — its pay pages, what is held,
  // what went back. null while it loads (and for cash, which has none).
  const [payments, setPayments] = useState(null);
  const [paymentsFailed, setPaymentsFailed] = useState(null);

  const loadPayments = () => {
    setPaymentsFailed(null);
    client
      .get(`/admin/orders/${orderId}/payments`)
      .then(({ data }) => setPayments(data))
      .catch((e) => setPaymentsFailed(apiError(e, "The payment ledger didn't load.")));
  };

  const open = () => {
    setFailed(null);
    client
      .get(`/orders/${orderId}`)
      .then(({ data }) => {
        setOrder(data);
        setStatus(data.status);
        if (data.payment_provider) loadPayments();
      })
      .catch((e) => setFailed(apiError(e, "That order didn't open.")));
  };

  // A refund or a provider check changed the money: take the fresh ledger
  // the action handed back (or re-read it), then the order and the rail —
  // the badge, the timeline and the list all follow the provider's word.
  const paymentsUpdated = (fresh) => {
    if (fresh) setPayments(fresh);
    open();
    onChanged();
  };

  useEffect(() => {
    open();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderId]);

  if (!order) {
    return (
      <div className="p-6">
        {failed ? (
          <>
            <LoadError message={failed} onRetry={open} />
            <div className="text-center">
              <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
                Close
              </button>
            </div>
          </>
        ) : (
          <Loading label="Opening the order…" />
        )}
      </div>
    );
  }

  const payment =
    PAYMENT_STATUSES[order.payment_status] ?? {
      label: statusLabel(order.payment_status),
      badge: "badge-ghost",
    };
  const online = paysOnline(order.payment_method);
  const provider = providerLabel(order.payment_provider);
  // Once a provider has taken money for the order, its payment status is
  // the provider's to set — the hand labels below step aside (the server
  // refuses them anyway). Until the ledger has loaded, assume it has.
  const providerHolds = online && (payments ? providerHoldsMoney(payments) : !paymentsFailed);
  // What a cancellation sends back: everything the provider still holds.
  const heldAmount = payments ? payments.refundable_amount : order.total;

  const sendStatus = async () => {
    setBusy(true);
    try {
      const { data } = await client.put(`/admin/orders/${order.id}/status`, {
        status,
        comment: comment.trim() || null,
        notify,
      });
      setOrder(data);
      setStatus(data.status);
      setComment("");
      onChanged();
      // A cancellation refunds what the provider holds — show it at once.
      if (data.payment_provider) loadPayments();
      toast.success(
        `Order moved to “${statusLabel(data.status)}”${notify ? " — customer emailed" : ""}`
      );
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setBusy(false);
      setConfirmingCancel(false);
    }
  };

  const applyStatus = (e) => {
    e.preventDefault();
    if (busy) return;
    if (status === "cancelled") setConfirmingCancel(true);
    else sendStatus();
  };

  const sendPayment = async (payment_status) => {
    if (paymentBusy) return;
    setPaymentBusy(true);
    try {
      const { data } = await client.put(`/admin/orders/${order.id}/payment`, { payment_status });
      setOrder(data);
      onChanged();
      toast.success(
        `Payment marked ${PAYMENT_STATUSES[payment_status]?.label ?? statusLabel(payment_status)}`
      );
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setPaymentBusy(false);
      setConfirmingPayment(null);
    }
  };

  // These buttons only relabel the book — they never reach a provider. On
  // a cash order that is the whole job. On an online order they exist only
  // while no provider payment is on record (then the label and the money
  // can part company, so it asks first and says so); once Stripe or PayPal
  // holds the money, the Refund button in the payment panel is the way.
  const setPayment = (payment_status) => {
    if (online) setConfirmingPayment(payment_status);
    else sendPayment(payment_status);
  };

  const PAYMENT_RELABEL = {
    paid: {
      title: "Mark this online order paid by hand?",
      confirm: "Mark paid",
      body: `Online orders mark themselves paid the moment ${provider} confirms the payment — “Check with ${provider}” in the payment panel asks it directly. Marking it by hand only changes the label here: use it only if the money really arrived another way.`,
    },
    refunded: {
      title: "Mark this order refunded by hand?",
      confirm: "Mark refunded",
      body: `This only changes the label in the book — nothing is sent back through ${provider}.`,
    },
    pending: {
      title: "Reset this payment to pending?",
      confirm: "Reset to pending",
      body: "This only changes the label — it does not move any money.",
    },
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-start justify-between gap-3 border-b border-base-300 p-5">
        <div>
          <h3 className="font-display text-xl font-bold">{order.order_number}</h3>
          <p className="text-xs opacity-70">
            {[order.customer_name, order.customer_email, formatDateTime(order.created_at)].filter(Boolean).join(" · ")}
          </p>
        </div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
          Close
        </button>
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto p-5">
        {/* Badges */}
        <div className="flex flex-wrap gap-2">
          <span className={`badge badge-lg ${statusBadge(order.status)} border`}>
            {statusLabel(order.status)}
          </span>
          <span className={`badge badge-lg ${payment.badge} border`}>{payment.label}</span>
          <span className="badge badge-lg badge-ghost border">{PAYMENT_METHODS[order.payment_method]}</span>
        </div>

        {/* Items */}
        <section>
          <p className="font-display mb-2 font-semibold">Items</p>
          <ul className="divide-y divide-base-200 text-sm">
            {order.items.map((i) => (
              <li key={i.id} className="flex justify-between gap-3 py-2">
                <span className="font-semibold">{i.product_name} <span className="opacity-70">× {i.quantity}</span></span>
                <span className="font-bold">{money(i.subtotal, order.currency)}</span>
              </li>
            ))}
          </ul>
          <div className="mt-2 space-y-1 text-sm">
            <div className="flex justify-between"><span className="opacity-70">Subtotal</span><span>{money(order.subtotal, order.currency)}</span></div>
            {order.discount_amount > 0 && (
              <div className="flex justify-between text-secondary">
                <span>Discount {order.coupon_code ? `(${order.coupon_code})` : ""}</span>
                <span>−{money(order.discount_amount, order.currency)}</span>
              </div>
            )}
            {/* No row while no tax was charged — and in VAT-inclusive mode the
                figure is already inside the lines, so it says so. */}
            {order.tax > 0 && (
              <div className="flex justify-between"><span className="opacity-70">{order.tax_included ? "VAT (included)" : "Tax"}</span><span>{money(order.tax, order.currency)}</span></div>
            )}
            <div className="flex justify-between"><span className="opacity-70">Delivery</span><span>{order.shipping_fee === 0 ? "FREE" : money(order.shipping_fee, order.currency)}</span></div>
            <div className="flex justify-between border-t border-dashed border-base-300 pt-1 font-bold">
              <span>Total</span><span>{money(order.total, order.currency)}</span>
            </div>
          </div>
        </section>

        {/* The provider's side of the money — online orders only */}
        {online && (
          <section>
            <PaymentsPanel
              order={order}
              payments={payments}
              failed={paymentsFailed}
              onRetry={loadPayments}
              onUpdated={paymentsUpdated}
            />
          </section>
        )}

        {/* Address + note */}
        <section className="rounded-box bg-base-200/60 p-4 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="font-display font-semibold">Deliver to</p>
            {/* The day the customer asked for at checkout — the desk plans
                the bake by it, so it leads the block. */}
            {order.delivery_date ? (
              <span className="badge badge-accent text-accent-content whitespace-nowrap">
                <CalendarHeart size={13} className="mr-1" aria-hidden="true" />
                Wanted {formatDate(order.delivery_date)}
              </span>
            ) : (
              <span className="text-xs opacity-70">Next delivery run</span>
            )}
          </div>
          <p className="mt-1 opacity-80">
            {order.recipient}{order.phone ? ` · ${order.phone}` : ""}<br />
            {order.line1}{order.line2 ? `, ${order.line2}` : ""}<br />
            {order.city}, {order.state} {order.postal_code}, {order.country}
          </p>
          {order.note && <p className="mt-2 text-xs italic opacity-70">“{order.note}”</p>}
        </section>

        {/* Timeline */}
        <section>
          <p className="font-display mb-2 font-semibold">Bake log</p>
          <ol className="space-y-2 text-sm">
            {order.timeline.map((e) => (
              <li key={e.id} className="opacity-80">
                <span className="font-semibold">{statusLabel(e.status)}</span>
                <span className="text-xs"> · {formatDateTime(e.created_at)}{e.comment ? ` — ${e.comment}` : ""}</span>
              </li>
            ))}
          </ol>
        </section>
      </div>

      {/* Controls */}
      <div className="space-y-3 border-t border-base-300 bg-base-200/60 p-5">
        <form onSubmit={applyStatus} className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <select
              className="select select-bordered select-sm w-44"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              aria-label="New status"
            >
              {ORDER_STATUSES.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
            <input
              className="input input-bordered input-sm min-w-36 flex-1"
              placeholder="Note for the log (optional)"
              aria-label="Note for the log (optional)"
              maxLength={200}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
            <button type="submit" className="btn btn-primary btn-sm" disabled={busy || status === order.status}>
              {busy ? <span className="loading loading-spinner loading-xs" /> : "Update status"}
            </button>
          </div>
          {/* The mailbox switch — one dial per change, no hidden memory of
              the last one. */}
          <label className="label cursor-pointer justify-start gap-3 py-0 text-xs">
            <input
              type="checkbox"
              className="toggle toggle-secondary toggle-sm"
              checked={notify}
              onChange={(e) => setNotify(e.target.checked)}
            />
            <span className="label-text flex items-center gap-1.5 text-sm">
              <Mail size={14} className="shrink-0 text-secondary" />
              {notify ? "Email the customer about this change" : "Quiet change — no email"}
            </span>
          </label>
        </form>
        {providerHolds ? (
          <p className="text-xs opacity-70">
            Payment status follows {provider} — to send money back, use <strong>Refund</strong> in
            the payment panel above.
          </p>
        ) : (
          /* Labels only — these never move money (see setPayment). */
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="opacity-70">Payment label:</span>
            {order.payment_status !== "paid" && (
              <button type="button" className="btn btn-success btn-xs" onClick={() => setPayment("paid")} disabled={paymentBusy}>
                Mark paid
              </button>
            )}
            {order.payment_status === "paid" && (
              <button type="button" className="btn btn-warning btn-xs" onClick={() => setPayment("refunded")} disabled={paymentBusy}>
                Mark refunded
              </button>
            )}
            {order.payment_status !== "pending" && (
              <button type="button" className="btn btn-ghost btn-xs" onClick={() => setPayment("pending")} disabled={paymentBusy}>
                Reset to pending
              </button>
            )}
          </div>
        )}
      </div>

      {confirmingCancel && (
        <ConfirmDialog
          title={`Cancel order ${order.order_number}?`}
          confirmLabel="Yes, cancel the order"
          cancelLabel="Keep the order"
          busy={busy}
          onConfirm={sendStatus}
          onCancel={() => setConfirmingCancel(false)}
        >
          <p>
            The reserved bakes go back on the shelf
            {providerHolds && heldAmount > 0
              ? ` and ${money(heldAmount, order.currency)} is refunded through ${provider}`
              : !online && order.payment_status === "paid"
                ? " and the payment is marked refunded — hand the cash back yourself"
                : ""}
            {notify ? ", and the customer is emailed" : ""}.
          </p>
          <p className="mt-2 font-semibold">A cancelled order can&rsquo;t be reopened.</p>
        </ConfirmDialog>
      )}

      {confirmingPayment && (
        <ConfirmDialog
          title={PAYMENT_RELABEL[confirmingPayment].title}
          confirmLabel={PAYMENT_RELABEL[confirmingPayment].confirm}
          cancelLabel="Leave it"
          busy={paymentBusy}
          onConfirm={() => sendPayment(confirmingPayment)}
          onCancel={() => setConfirmingPayment(null)}
        >
          {PAYMENT_RELABEL[confirmingPayment].body}
        </ConfirmDialog>
      )}
    </div>
  );
}

/** Drawer for one catering lead: the event, the ask, the email conversation
    and the baker's notes. */
function BulkDrawer({ enquiry, onClose, onChanged, onDeleted }) {
  const toast = useToast();
  useScrollLock();
  const [status, setStatus] = useState(enquiry.status);
  const [note, setNote] = useState(enquiry.admin_note ?? "");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);

  const occasion = EVENT_TYPES.find((t) => t.value === enquiry.event_type)?.label ?? enquiry.event_type;
  const replies = enquiry.replies ?? [];

  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const { data } = await client.put(`/admin/bulk-orders/${enquiry.id}`, {
        status,
        admin_note: note.trim(),
      });
      onChanged(data);
      toast.success(`Enquiry moved to “${bulkLabel(data.status)}”`);
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setBusy(false);
    }
  };

  const sendReply = async (e) => {
    e.preventDefault();
    if (!reply.trim()) return;
    setSending(true);
    try {
      const { data } = await client.post(`/admin/bulk-orders/${enquiry.id}/reply`, {
        message: reply,
      });
      setReply("");
      onChanged(data);
      setStatus(data.status); // a first reply bumps a new lead to "contacted"
      toast.success(`Reply emailed to ${enquiry.email}`);
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setSending(false);
    }
  };

  const [removing, setRemoving] = useState(false);
  const remove = async () => {
    if (removing) return; // a double-click must not send a second DELETE
    setRemoving(true);
    try {
      await client.delete(`/admin/bulk-orders/${enquiry.id}`);
      onDeleted();
      toast.info("Enquiry deleted");
    } catch (err) {
      toast.error(apiError(err));
      setRemoving(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-start justify-between gap-3 border-b border-base-300 p-5">
        <div>
          <h3 className="font-display text-xl font-bold">{enquiry.name}</h3>
          <p className="text-xs opacity-70">
            {enquiry.email}{enquiry.phone ? ` · ${enquiry.phone}` : ""} · sent {formatDateTime(enquiry.created_at)}
          </p>
        </div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
          Close
        </button>
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto p-5">
        <div className="flex flex-wrap gap-2">
          <span className={`badge badge-lg ${bulkBadge(enquiry.status)} border`}>
            {bulkLabel(enquiry.status)}
          </span>
          <span className="badge badge-lg badge-ghost border">{occasion}</span>
          <span className="badge badge-lg badge-ghost border">
            <Users size={13} className="mr-1" />
            {enquiry.guests} guests
          </span>
        </div>

        <section className="rounded-box bg-base-200/60 p-4 text-sm">
          <p className="flex items-center gap-2 font-display font-semibold">
            <CalendarHeart size={16} className="text-secondary" />
            {formatDate(enquiry.event_date)}
          </p>
          {enquiry.items_note && (
            <p className="mt-2 opacity-80">
              <span className="font-semibold">They have in mind: </span>
              {enquiry.items_note}
            </p>
          )}
          {enquiry.message && (
            <p className="mt-2 opacity-80 italic">“{enquiry.message}”</p>
          )}
        </section>

        {replies.length > 0 && (
          <section>
            <p className="font-display mb-2 font-semibold">Sent to their inbox</p>
            <ul className="space-y-2">
              {replies.map((r) => (
                <li
                  key={r.id}
                  className="rounded-box border border-secondary/30 bg-secondary/5 p-3 text-sm"
                >
                  <p className="whitespace-pre-line">{r.message}</p>
                  <p className="mt-1.5 text-xs opacity-65">
                    emailed {formatDateTime(r.created_at)}
                  </p>
                </li>
              ))}
            </ul>
          </section>
        )}

        {enquiry.admin_note && (
          <section className="rounded-box border border-dashed border-base-300 p-4 text-sm">
            <p className="font-display font-semibold">Your notes</p>
            <p className="mt-1 whitespace-pre-line opacity-80">{enquiry.admin_note}</p>
          </section>
        )}
      </div>

      {/* Controls */}
      <div className="space-y-4 border-t border-base-300 bg-base-200/60 p-5">
        <form onSubmit={sendReply} className="space-y-2">
          <label className="label text-xs font-semibold" htmlFor="bulk-reply">
            Reply by email
          </label>
          <textarea
            id="bulk-reply"
            className="textarea textarea-bordered w-full"
            rows={3}
            maxLength={4000}
            placeholder="Here's what we'd suggest for 40 guests…"
            value={reply}
            onChange={(e) => setReply(e.target.value)}
          />
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs opacity-65">
              Goes to their inbox · a first reply marks the lead “contacted”
            </p>
            <button
              type="submit"
              className="btn btn-secondary btn-sm shrink-0 text-secondary-content"
              disabled={sending || !reply.trim()}
            >
              {sending ? (
                <span className="loading loading-spinner loading-xs" />
              ) : (
                <Send size={14} />
              )}
              Send reply
            </button>
          </div>
        </form>

        <form onSubmit={save} className="space-y-2">
          <div className="flex items-center gap-2">
            <select
              className="select select-bordered select-sm w-44"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              aria-label="Enquiry status"
            >
              {BULK_STATUSES.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
            <button
              type="submit"
              className="btn btn-primary btn-sm"
              disabled={busy || (status === enquiry.status && note.trim() === (enquiry.admin_note ?? ""))}
            >
              {busy ? <span className="loading loading-spinner loading-xs" /> : "Save"}
            </button>
          </div>
          <textarea
            className="textarea textarea-bordered w-full"
            rows={2}
            maxLength={2000}
            placeholder="Private note — quote given, portions agreed, call-back Thursday…"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            aria-label="Private admin note"
          />
        </form>
        <button
          type="button"
          className="btn btn-ghost btn-xs text-error"
          onClick={() => setConfirming(true)}
        >
          <Trash2 size={14} />
          Delete enquiry
        </button>
      </div>

      {confirming && (
        <ConfirmDialog
          title={`Delete the enquiry from ${enquiry.name}?`}
          confirmLabel="Yes, delete"
          busy={removing}
          onConfirm={remove}
          onCancel={() => setConfirming(false)}
        >
          This can&rsquo;t be undone — the lead and your notes on it go for
          good.
        </ConfirmDialog>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Tab 1 — the everyday order rail                                     */
/* ------------------------------------------------------------------ */

function OrdersTab() {
  const [params, setParams] = useSearchParams();
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(null);
  const [search, setSearch] = useState(params.get("search") ?? "");
  const [selectedId, setSelectedId] = useState(null);
  const toast = useToast();

  const status = params.get("status") ?? "";
  const page = Number(params.get("page") ?? 1);
  const searchParam = params.get("search") ?? "";

  const load = () => {
    const query = { page, per_page: 15 };
    if (status) query.status = status;
    if (searchParam) query.search = searchParam;
    setFailed(null);
    client
      .get("/admin/orders", { params: query })
      .then(({ data }) => setData(data))
      .catch((e) => setFailed(apiError(e, "The order rail didn't load.")));
  };

  useEffect(() => {
    setData(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, page, searchParam]);

  // The box follows the address bar: the bell and the dashboard deep-link
  // here with ?search=…, and "Reset" clears it — the list changed either
  // way, so the box must say the same thing the list is showing.
  useEffect(() => setSearch(searchParam), [searchParam]);

  const submitSearch = (e) => {
    e.preventDefault();
    const next = new URLSearchParams(params);
    if (search.trim()) next.set("search", search.trim());
    else next.delete("search");
    next.delete("page");
    setParams(next);
  };

  const setStatusFilter = (value) => {
    const next = new URLSearchParams(params);
    if (value) next.set("status", value);
    else next.delete("status");
    next.delete("page");
    setParams(next);
  };

  const goPage = (p) => {
    const next = new URLSearchParams(params);
    if (p > 1) next.set("page", p);
    else next.delete("page");
    setParams(next);
  };

  const exportCsv = async () => {
    try {
      const res = await client.get("/admin/orders/export", { responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      const link = document.createElement("a");
      link.href = url;
      link.download = "bakehouse-orders.csv";
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(apiError(err, "Export failed"));
    }
  };

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <form onSubmit={submitSearch} className="join">
          <input
            className="input input-bordered input-sm join-item w-56"
            placeholder="Search order # or customer…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search orders"
          />
          <button type="submit" className="btn btn-primary btn-sm join-item">Search</button>
        </form>
        <select
          className="select select-bordered select-sm"
          value={status}
          onChange={(e) => setStatusFilter(e.target.value)}
          aria-label="Filter by status"
        >
          <option value="">All statuses</option>
          {ORDER_STATUSES.map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
        <Link to="/admin/orders" className="btn btn-ghost btn-sm">Reset</Link>
        <button type="button" className="btn btn-ghost btn-sm ml-auto" onClick={exportCsv}>
          Export CSV
        </button>
      </div>

      {failed ? (
        <LoadError message={failed} onRetry={load} />
      ) : data === null ? (
        <Loading label="Fetching the order rail…" />
      ) : (
        <>
          <div className="overflow-x-auto rounded-box glass shadow-sm ring-1 ring-base-300/60">
            <table className="table table-sm">
              <thead>
                {/* On a phone the rail is three columns — the order (with
                    who it is for), where it stands (with what it comes to),
                    and the way in — so "Manage" is on the screen, not a
                    sideways scroll away. The rest joins from a tablet up;
                    the two dates wait for a wide screen (at `lg` the desk's
                    sidebar arrives and the table has less room, not more). */}
                <tr className="text-xs uppercase opacity-65">
                  <th>Order</th>
                  <th className="hidden sm:table-cell">Recipient</th>
                  <th>Status</th>
                  <th className="hidden md:table-cell">Payment</th>
                  <th className="hidden text-right sm:table-cell">Total</th>
                  <th className="hidden xl:table-cell">Wanted for</th>
                  <th className="hidden text-right xl:table-cell">Placed</th>
                  <th><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {data.orders.map((o) => (
                  <tr key={o.id} className="hover:bg-base-200/50">
                    <td className="whitespace-nowrap">
                      <span className="font-bold">{o.order_number}</span>
                      <span className="block max-w-36 truncate text-xs opacity-70 sm:hidden">{o.recipient}</span>
                    </td>
                    <td className="hidden max-w-36 truncate sm:table-cell">{o.recipient}</td>
                    <td>
                      <span className={`badge badge-sm ${statusBadge(o.status)} border whitespace-nowrap`}>{statusLabel(o.status)}</span>
                      <span className="figures mt-1 block text-xs font-bold sm:hidden">{money(o.total, o.currency)}</span>
                    </td>
                    <td className="hidden text-xs md:table-cell">
                      <span className={`badge badge-sm ${PAYMENT_STATUSES[o.payment_status]?.badge ?? "badge-ghost"} whitespace-nowrap`}>
                        {PAYMENT_STATUSES[o.payment_status]?.label ?? statusLabel(o.payment_status)}
                      </span>
                      <span className="mt-0.5 block whitespace-nowrap opacity-70">
                        {PAYMENT_METHODS[o.payment_method] ?? o.payment_method}
                      </span>
                    </td>
                    <td className="figures hidden text-right font-bold whitespace-nowrap sm:table-cell">{money(o.total, o.currency)}</td>
                    {/* The delivery day the customer picked at checkout —
                        blank means "the next run", which the dash says. */}
                    <td className="hidden text-xs whitespace-nowrap xl:table-cell">
                      {o.delivery_date ? (
                        <span className="font-semibold">{formatDate(o.delivery_date)}</span>
                      ) : (
                        <span className="opacity-40" title="No day picked — next delivery run">—</span>
                      )}
                    </td>
                    <td className="hidden text-right text-xs whitespace-nowrap opacity-70 xl:table-cell">{formatDateTime(o.created_at)}</td>
                    <td className="text-right">
                      {/* The word from 360px up; on a narrower phone the
                          arrow alone (still named), so the row fits. */}
                      <button
                        type="button"
                        className="btn btn-ghost btn-xs"
                        onClick={() => setSelectedId(o.id)}
                        aria-label={`Manage order ${o.order_number}`}
                      >
                        <span className="max-[359px]:hidden">Manage</span>
                        <ChevronRight size={16} className="min-[360px]:hidden" aria-hidden="true" />
                      </button>
                    </td>
                  </tr>
                ))}
                {data.orders.length === 0 && (
                  <tr>
                    <td colSpan={8} className="py-10 text-center text-sm opacity-70">
                      No orders match — try clearing the filters.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <Pager
            className="mt-4"
            label="Pages of orders"
            page={data.pagination.page}
            pages={data.pagination.total_pages}
            onPage={goPage}
          />
        </>
      )}

      {/* Drawer */}
      {selectedId !== null && (
        <DrawerFrame label="Order details" onClose={() => setSelectedId(null)}>
          <OrderDrawer
            orderId={selectedId}
            onClose={() => setSelectedId(null)}
            onChanged={load}
          />
        </DrawerFrame>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Tab 2 — event & catering enquiries                                  */
/* ------------------------------------------------------------------ */

function BulkTab() {
  const [params, setParams] = useSearchParams();
  const [items, setItems] = useState(null);
  const [failed, setFailed] = useState(null);
  const [selected, setSelected] = useState(null);
  const toast = useToast();

  const status = params.get("bulk_status") ?? "";

  const load = (filter = status) => {
    const query = {};
    if (filter) query.status = filter;
    setFailed(null);
    client
      .get("/admin/bulk-orders", { params: query })
      .then(({ data }) => setItems(data))
      .catch((e) => setFailed(apiError(e, "The enquiry rail didn't load.")));
  };

  useEffect(() => {
    setItems(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  const setStatusFilter = (value) => {
    const next = new URLSearchParams(params);
    if (value) next.set("bulk_status", value);
    else next.delete("bulk_status");
    setParams(next);
  };

  const replaceRow = (updated) =>
    setItems((rows) => rows.map((r) => (r.id === updated.id ? updated : r)));

  const exportCsv = async () => {
    try {
      const res = await client.get("/admin/bulk-orders/export", { responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      const link = document.createElement("a");
      link.href = url;
      link.download = "bakehouse-bulk-enquiries.csv";
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(apiError(err, "Export failed"));
    }
  };

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select
          className="select select-bordered select-sm"
          value={status}
          onChange={(e) => setStatusFilter(e.target.value)}
          aria-label="Filter enquiries by status"
        >
          <option value="">All enquiries</option>
          {BULK_STATUSES.map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
        {status && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setStatusFilter("")}>
            Reset
          </button>
        )}
        <button type="button" className="btn btn-ghost btn-sm ml-auto" onClick={exportCsv}>
          Export CSV
        </button>
      </div>

      {failed ? (
        <LoadError message={failed} onRetry={() => load()} />
      ) : items === null ? (
        <Loading label="Fetching the enquiry rail…" />
      ) : (
        <div className="overflow-x-auto rounded-box glass shadow-sm ring-1 ring-base-300/60">
          <table className="table table-sm">
            <thead>
              {/* Three columns on a phone, like the order rail: the event
                  (with who asked), where it stands (with the headcount) and
                  the way in. */}
              <tr className="text-xs uppercase opacity-65">
                <th>Event</th>
                <th className="hidden sm:table-cell">Customer</th>
                <th className="hidden text-right sm:table-cell">Guests</th>
                <th>Status</th>
                <th className="hidden text-right md:table-cell">Sent</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {items.map((e) => (
                <tr key={e.id} className="hover:bg-base-200/50">
                  <td>
                    <p className="font-bold whitespace-nowrap">{formatDate(e.event_date)}</p>
                    <p className="text-xs opacity-70">
                      {EVENT_TYPES.find((t) => t.value === e.event_type)?.label ?? e.event_type}
                    </p>
                    <p className="max-w-36 truncate text-xs opacity-70 sm:hidden">{e.name}</p>
                  </td>
                  <td className="hidden sm:table-cell">
                    <p className="max-w-44 truncate font-semibold">{e.name}</p>
                    <p className="max-w-44 truncate text-xs opacity-70">{e.email}</p>
                  </td>
                  <td className="hidden text-right font-bold sm:table-cell">{e.guests}</td>
                  <td>
                    <span className={`badge badge-sm ${bulkBadge(e.status)} border whitespace-nowrap`}>
                      {bulkLabel(e.status)}
                    </span>
                    <span className="mt-1 block text-xs opacity-70 sm:hidden">
                      {e.guests} {e.guests === 1 ? "guest" : "guests"}
                    </span>
                  </td>
                  <td className="hidden text-right text-xs whitespace-nowrap opacity-70 md:table-cell">
                    {formatDateTime(e.created_at)}
                  </td>
                  <td className="text-right">
                    <button
                      type="button"
                      className="btn btn-ghost btn-xs"
                      onClick={() => setSelected(e)}
                      aria-label={`Manage the enquiry from ${e.name}`}
                    >
                      <span className="max-[359px]:hidden">Manage</span>
                      <ChevronRight size={16} className="min-[360px]:hidden" aria-hidden="true" />
                    </button>
                  </td>
                </tr>
              ))}
              {items.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-10 text-center text-sm opacity-70">
                    No enquiries yet — the /bulk page feeds this rail.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Drawer */}
      {selected && (
        <DrawerFrame label="Catering enquiry" onClose={() => setSelected(null)}>
          <BulkDrawer
            key={selected.id}
            enquiry={selected}
            onClose={() => setSelected(null)}
            onChanged={(updated) => {
              replaceRow(updated);
              setSelected(updated);
              load();
            }}
            onDeleted={() => {
              setSelected(null);
              load();
            }}
          />
        </DrawerFrame>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Tab 3 — the payment check                                           */
/* ------------------------------------------------------------------ */

function PaymentsTab() {
  const [selectedId, setSelectedId] = useState(null);
  // Bumped whenever the desk acts on an order from here, so the report
  // re-reads the ledger and a settled line leaves the list.
  const [version, setVersion] = useState(0);

  return (
    <div>
      <ReconciliationReport onOpenOrder={setSelectedId} version={version} />
      {selectedId !== null && (
        <DrawerFrame label="Order details" onClose={() => setSelectedId(null)}>
          <OrderDrawer
            orderId={selectedId}
            onClose={() => setSelectedId(null)}
            onChanged={() => setVersion((v) => v + 1)}
          />
        </DrawerFrame>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */

const TABS = [
  { value: "orders", label: "Orders" },
  { value: "bulk", label: "Bulk enquiries" },
  { value: "payments", label: "Payment check" },
];

export default function AdminOrders() {
  const [params, setParams] = useSearchParams();

  const asked = params.get("tab");
  const tab = TABS.some((t) => t.value === asked) ? asked : "orders";

  const switchTab = (value) => {
    // A fresh sheet per tab — no stale filters carried across
    const next = new URLSearchParams();
    if (value !== "orders") next.set("tab", value);
    setParams(next);
  };

  return (
    <div>
      <div role="tablist" aria-label="Order desks" className="tabs tabs-boxed mb-5 inline-flex">
        {TABS.map((t) => (
          <button
            key={t.value}
            role="tab"
            aria-selected={tab === t.value}
            className={`tab ${tab === t.value ? "tab-active" : ""}`}
            onClick={() => switchTab(t.value)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "orders" ? <OrdersTab /> : tab === "bulk" ? <BulkTab /> : <PaymentsTab />}
    </div>
  );
}
