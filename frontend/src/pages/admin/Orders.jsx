import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { CalendarHeart, Mail, Send, Trash2, Users } from "lucide-react";
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
  statusBadge,
  statusLabel,
} from "../../lib/format";
import { useToast } from "../../context/ToastContext";
import ConfirmDialog from "../../components/ConfirmDialog";
import { Loading } from "../../components/Loading";
import { useScrollLock } from "../../components/Drawer";

/** Drawer with the full order + status/payment controls. */
function OrderDrawer({ orderId, onClose, onChanged }) {
  const toast = useToast();
  useScrollLock();
  const [order, setOrder] = useState(null);
  const [status, setStatus] = useState("");
  const [comment, setComment] = useState("");
  // Whether this dial is mailbox news — the baker's call, per change.
  const [notify, setNotify] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    client
      .get(`/orders/${orderId}`)
      .then(({ data }) => {
        setOrder(data);
        setStatus(data.status);
      })
      .catch((e) => toast.error(apiError(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderId]);

  if (!order) {
    return (
      <div className="p-6">
        <Loading label="Opening the order…" />
      </div>
    );
  }

  const payment =
    PAYMENT_STATUSES[order.payment_status] ?? {
      label: statusLabel(order.payment_status),
      badge: "badge-ghost",
    };

  const applyStatus = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const { data } = await client.put(`/admin/orders/${order.id}/status`, {
        status,
        comment: comment.trim() || null,
        notify,
      });
      setOrder(data);
      setComment("");
      onChanged();
      toast.success(
        `Order moved to “${statusLabel(data.status)}”${notify ? " — customer emailed" : ""}`
      );
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setBusy(false);
    }
  };

  const setPayment = async (payment_status) => {
    try {
      const { data } = await client.put(`/admin/orders/${order.id}/payment`, { payment_status });
      setOrder(data);
      onChanged();
      toast.success(
        `Payment marked ${PAYMENT_STATUSES[payment_status]?.label ?? statusLabel(payment_status)}`
      );
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-start justify-between gap-3 border-b border-base-300 p-5">
        <div>
          <h3 className="font-display text-xl font-bold">{order.order_number}</h3>
          <p className="text-xs opacity-60">
            {order.customer_name} · {order.customer_email} · {formatDateTime(order.created_at)}
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
                <span className="font-semibold">{i.product_name} <span className="opacity-60">× {i.quantity}</span></span>
                <span className="font-bold">{money(i.subtotal, order.currency)}</span>
              </li>
            ))}
          </ul>
          <div className="mt-2 space-y-1 text-sm">
            <div className="flex justify-between"><span className="opacity-60">Subtotal</span><span>{money(order.subtotal, order.currency)}</span></div>
            {order.discount_amount > 0 && (
              <div className="flex justify-between text-secondary">
                <span>Discount {order.coupon_code ? `(${order.coupon_code})` : ""}</span>
                <span>−{money(order.discount_amount, order.currency)}</span>
              </div>
            )}
            <div className="flex justify-between"><span className="opacity-60">Tax</span><span>{money(order.tax, order.currency)}</span></div>
            <div className="flex justify-between"><span className="opacity-60">Delivery</span><span>{order.shipping_fee === 0 ? "FREE" : money(order.shipping_fee, order.currency)}</span></div>
            <div className="flex justify-between border-t border-dashed border-base-300 pt-1 font-bold">
              <span>Total</span><span>{money(order.total, order.currency)}</span>
            </div>
          </div>
        </section>

        {/* Address + note */}
        <section className="rounded-box bg-base-200/60 p-4 text-sm">
          <p className="font-display font-semibold">Deliver to</p>
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
        <div className="flex items-center gap-2 text-sm">
          <span className="opacity-60">Payment:</span>
          {order.payment_status !== "paid" && (
            <button type="button" className="btn btn-success btn-xs" onClick={() => setPayment("paid")}>
              Mark paid
            </button>
          )}
          {order.payment_status === "paid" && (
            <button type="button" className="btn btn-warning btn-xs" onClick={() => setPayment("refunded")}>
              Refund
            </button>
          )}
          {order.payment_status !== "pending" && (
            <button type="button" className="btn btn-ghost btn-xs" onClick={() => setPayment("pending")}>
              Reset to pending
            </button>
          )}
        </div>
      </div>
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

  const remove = async () => {
    try {
      await client.delete(`/admin/bulk-orders/${enquiry.id}`);
      onDeleted();
      toast.info("Enquiry deleted");
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-start justify-between gap-3 border-b border-base-300 p-5">
        <div>
          <h3 className="font-display text-xl font-bold">{enquiry.name}</h3>
          <p className="text-xs opacity-60">
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
                  <p className="mt-1.5 text-xs opacity-50">
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
            <p className="text-xs opacity-50">
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
  const [search, setSearch] = useState(params.get("search") ?? "");
  const [selectedId, setSelectedId] = useState(null);
  const toast = useToast();

  const status = params.get("status") ?? "";
  const page = Number(params.get("page") ?? 1);

  const load = () => {
    const query = { page, per_page: 15 };
    if (status) query.status = status;
    if (params.get("search")) query.search = params.get("search");
    client.get("/admin/orders", { params: query }).then(({ data }) => setData(data));
  };

  useEffect(() => {
    setData(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, page, params.get("search")]);

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

      {data === null ? (
        <Loading label="Fetching the order rail…" />
      ) : (
        <>
          <div className="overflow-x-auto rounded-box glass shadow-sm ring-1 ring-base-300/60">
            <table className="table table-sm">
              <thead>
                <tr className="text-xs uppercase opacity-50">
                  <th>Order</th>
                  <th>Recipient</th>
                  <th>Status</th>
                  <th>Payment</th>
                  <th className="text-right">Total</th>
                  <th className="text-right">Placed</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.orders.map((o) => (
                  <tr key={o.id} className="hover:bg-base-200/50">
                    <td className="font-bold">{o.order_number}</td>
                    <td className="max-w-36 truncate">{o.recipient}</td>
                    <td>
                      <span className={`badge badge-sm ${statusBadge(o.status)} border`}>{statusLabel(o.status)}</span>
                    </td>
                    <td className="text-xs">
                      <span className={`badge badge-sm ${PAYMENT_STATUSES[o.payment_status]?.badge ?? "badge-ghost"}`}>
                        {PAYMENT_STATUSES[o.payment_status]?.label ?? statusLabel(o.payment_status)}
                      </span>
                    </td>
                    <td className="text-right font-bold">{money(o.total, o.currency)}</td>
                    <td className="text-right text-xs whitespace-nowrap opacity-60">{formatDateTime(o.created_at)}</td>
                    <td className="text-right">
                      <button
                        type="button"
                        className="btn btn-ghost btn-xs"
                        onClick={() => setSelectedId(o.id)}
                      >
                        Manage
                      </button>
                    </td>
                  </tr>
                ))}
                {data.orders.length === 0 && (
                  <tr>
                    <td colSpan={7} className="py-10 text-center text-sm opacity-60">
                      No orders match — try clearing the filters.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {data.pagination.total_pages > 1 && (
            <div className="mt-4 flex justify-center">
              <div className="join">
                <button className="btn join-item btn-sm" disabled={page <= 1} onClick={() => goPage(page - 1)}>«</button>
                <button className="btn join-item btn-sm pointer-events-none">
                  Page {data.pagination.page} / {data.pagination.total_pages}
                </button>
                <button
                  className="btn join-item btn-sm"
                  disabled={page >= data.pagination.total_pages}
                  onClick={() => goPage(page + 1)}
                >
                  »
                </button>
              </div>
            </div>
          )}
        </>
      )}

      {/* Drawer */}
      {selectedId !== null && (
        <div className="fixed inset-0 z-[70] flex justify-end">
          <div
            className="absolute inset-0 bg-black/40 backdrop-blur-sm"
            onClick={() => setSelectedId(null)}
            aria-hidden="true"
          />
          <div className="animate-pop relative h-full w-full max-w-md overflow-hidden bg-base-100 shadow-2xl">
            <OrderDrawer
              orderId={selectedId}
              onClose={() => setSelectedId(null)}
              onChanged={load}
            />
          </div>
        </div>
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
  const [selected, setSelected] = useState(null);
  const toast = useToast();

  const status = params.get("bulk_status") ?? "";

  const load = (filter = status) => {
    const query = {};
    if (filter) query.status = filter;
    client.get("/admin/bulk-orders", { params: query }).then(({ data }) => setItems(data));
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

      {items === null ? (
        <Loading label="Fetching the enquiry rail…" />
      ) : (
        <div className="overflow-x-auto rounded-box glass shadow-sm ring-1 ring-base-300/60">
          <table className="table table-sm">
            <thead>
              <tr className="text-xs uppercase opacity-50">
                <th>Event</th>
                <th>Customer</th>
                <th className="text-right">Guests</th>
                <th>Status</th>
                <th className="text-right">Sent</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((e) => (
                <tr key={e.id} className="hover:bg-base-200/50">
                  <td>
                    <p className="font-bold whitespace-nowrap">{formatDate(e.event_date)}</p>
                    <p className="text-xs opacity-60">
                      {EVENT_TYPES.find((t) => t.value === e.event_type)?.label ?? e.event_type}
                    </p>
                  </td>
                  <td>
                    <p className="max-w-44 truncate font-semibold">{e.name}</p>
                    <p className="max-w-44 truncate text-xs opacity-60">{e.email}</p>
                  </td>
                  <td className="text-right font-bold">{e.guests}</td>
                  <td>
                    <span className={`badge badge-sm ${bulkBadge(e.status)} border`}>
                      {bulkLabel(e.status)}
                    </span>
                  </td>
                  <td className="text-right text-xs whitespace-nowrap opacity-60">
                    {formatDateTime(e.created_at)}
                  </td>
                  <td className="text-right">
                    <button
                      type="button"
                      className="btn btn-ghost btn-xs"
                      onClick={() => setSelected(e)}
                    >
                      Manage
                    </button>
                  </td>
                </tr>
              ))}
              {items.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-10 text-center text-sm opacity-60">
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
        <div className="fixed inset-0 z-[70] flex justify-end">
          <div
            className="absolute inset-0 bg-black/40 backdrop-blur-sm"
            onClick={() => setSelected(null)}
            aria-hidden="true"
          />
          <div className="animate-pop relative h-full w-full max-w-md overflow-hidden bg-base-100 shadow-2xl">
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
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */

export default function AdminOrders() {
  const [params, setParams] = useSearchParams();

  const tab = params.get("tab") === "bulk" ? "bulk" : "orders";

  const switchTab = (value) => {
    // A fresh sheet per tab — no stale filters carried across
    const next = new URLSearchParams();
    if (value === "bulk") next.set("tab", "bulk");
    setParams(next);
  };

  return (
    <div>
      <div role="tablist" aria-label="Order desks" className="tabs tabs-boxed mb-5 inline-flex">
        <button
          role="tab"
          aria-selected={tab === "orders"}
          className={`tab ${tab === "orders" ? "tab-active" : ""}`}
          onClick={() => switchTab("orders")}
        >
          Orders
        </button>
        <button
          role="tab"
          aria-selected={tab === "bulk"}
          className={`tab ${tab === "bulk" ? "tab-active" : ""}`}
          onClick={() => switchTab("bulk")}
        >
          Bulk enquiries
        </button>
      </div>

      {tab === "orders" ? (
        <OrdersTab />
      ) : (
        <BulkTab />
      )}
    </div>
  );
}
