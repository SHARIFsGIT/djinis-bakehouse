import { useEffect, useId, useRef, useState } from "react";
import { CircleAlert, CircleCheck, FlaskConical, RefreshCw, ShieldAlert, Undo2 } from "lucide-react";
import client, { apiError } from "../../api/client";
import {
  formatDateTime,
  money,
  parseAmountToCents,
  providerLabel,
  REFUND_STATUSES,
  statusLabel,
  TRANSACTION_STATUSES,
} from "../../lib/format";
import { useToast } from "../../context/ToastContext";
import { LoadError, Loading } from "../../components/Loading";

/* The desk's side of the payment ledger: what a provider holds for an order,
   real refunds through that provider, and the report of what doesn't add up.

   One rule shapes every control here — a refund is something Stripe or
   PayPal does, never something the desk declares. So the buttons ask the
   provider, and what the screen says afterwards is what the provider said. */

/* Ledger states in which a provider holds (or held) money for the order. */
const HOLDING_STATES = ["succeeded", "partially_refunded", "refunded", "disputed"];

/** True once a provider has taken money for this order — from then on its
    payment status follows the provider, and the hand labels step aside. */
export const providerHoldsMoney = (payments) =>
  Boolean(payments?.transactions?.some((tx) => HOLDING_STATES.includes(tx.status)));

const newestRefund = (payments) =>
  (payments?.transactions ?? [])
    .flatMap((tx) => tx.refunds)
    .reduce((latest, refund) => (latest && latest.id > refund.id ? latest : refund), null);

/* ------------------------------------------------------------------ */
/* Refund dialog                                                       */
/* ------------------------------------------------------------------ */

/** Asks how much to send back and why, then sends it. Built like the house
    ConfirmDialog (same shell, Escape and scrim back out, focus comes home)
    with the two fields a refund needs. */
function RefundDialog({ order, payments, onClose, onDone }) {
  const toast = useToast();
  const titleId = useId();
  const amountRef = useRef(null);
  // Captured while rendering, so closing hands focus back to the opener.
  const opener = useRef(typeof document === "undefined" ? null : document.activeElement);
  const provider = providerLabel(payments.provider);
  const remainingCents = Math.round(payments.refundable_amount * 100);
  const [amount, setAmount] = useState((remainingCents / 100).toFixed(2));
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [refused, setRefused] = useState(null);

  useEffect(() => {
    amountRef.current?.select();
    const returnTo = opener.current;
    return () => returnTo?.focus?.();
  }, []);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "Escape" || busy) return;
      e.stopPropagation(); // the top layer — the drawer beneath stays open
      onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [busy, onClose]);

  const cents = parseAmountToCents(amount);
  const problem =
    cents === null
      ? "Enter an amount like 4.50"
      : cents <= 0
        ? "A refund needs an amount above zero"
        : cents > remainingCents
          ? `Only ${money(remainingCents / 100, order.currency)} of this payment is left to refund`
          : null;
  const everything = cents === remainingCents;

  const submit = async (e) => {
    e.preventDefault();
    if (busy || problem) return;
    setBusy(true);
    setRefused(null);
    try {
      const { data } = await client.post(`/admin/orders/${order.id}/refunds`, {
        amount: cents / 100,
        reason: reason.trim() || null,
      });
      if (newestRefund(data)?.status === "pending") {
        toast.info(`Refund requested — ${provider} has yet to confirm it`);
      } else {
        toast.success(`${money(cents / 100, order.currency)} sent back through ${provider}`);
      }
      onDone(data);
    } catch (err) {
      if (err?.response?.status === 502) {
        // The provider said no. The attempt is on the order's record, so
        // the panel behind this dialog is where to read what happened.
        toast.error(apiError(err, `${provider} did not accept the refund`));
        onDone(null);
        return;
      }
      setRefused(apiError(err, "The refund could not be sent"));
      setBusy(false);
    }
  };

  return (
    <div className="modal modal-open" role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <form className="modal-box max-w-sm" onSubmit={submit} noValidate>
        <h3 id={titleId} className="font-display text-lg font-bold">
          Refund order {order.order_number}
        </h3>
        <p className="mt-1 text-sm opacity-70">
          {provider} still holds {money(remainingCents / 100, order.currency)} of this payment.
          What you send back goes to the customer through {provider} and can&rsquo;t be recalled.
        </p>

        <label className="label mt-4 pb-1 text-xs font-semibold" htmlFor={`${titleId}-amount`}>
          Amount to send back ({String(order.currency ?? "").toUpperCase()})
        </label>
        <div className="flex items-center gap-2">
          <input
            id={`${titleId}-amount`}
            ref={amountRef}
            className={`input input-bordered input-sm w-32 ${problem ? "input-error" : ""}`}
            inputMode="decimal"
            autoComplete="off"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            aria-invalid={Boolean(problem)}
            aria-describedby={`${titleId}-help`}
            disabled={busy}
          />
          {!everything && (
            <button
              type="button"
              className="btn btn-ghost btn-xs"
              onClick={() => setAmount((remainingCents / 100).toFixed(2))}
              disabled={busy}
            >
              Everything left
            </button>
          )}
        </div>
        <p id={`${titleId}-help`} className={`mt-1 text-xs ${problem ? "text-error" : "opacity-60"}`}>
          {problem ?? (everything ? "All that is still held." : "Part of what is still held.")}
        </p>

        <label className="label mt-3 pb-1 text-xs font-semibold" htmlFor={`${titleId}-reason`}>
          Reason (optional)
        </label>
        <input
          id={`${titleId}-reason`}
          className="input input-bordered input-sm w-full"
          maxLength={255}
          placeholder="One croissant arrived squashed"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          aria-describedby={`${titleId}-reason-help`}
          disabled={busy}
        />
        <p id={`${titleId}-reason-help`} className="mt-1 text-xs opacity-60">
          Kept on the refund&rsquo;s record for the desk — the customer does not see it.
        </p>

        {refused && (
          <p className="mt-3 text-sm text-error" role="alert">
            {refused}
          </p>
        )}
        <p className="mt-3 text-xs opacity-60">
          The customer is emailed once {provider} confirms the refund.
        </p>

        <div className="modal-action">
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} disabled={busy}>
            Keep the money
          </button>
          <button type="submit" className="btn btn-error btn-sm" disabled={busy || Boolean(problem)}>
            {busy ? (
              <span className="loading loading-spinner loading-xs" aria-label="Sending the refund" />
            ) : (
              `Send ${problem ? "it" : money(cents / 100, order.currency)} back`
            )}
          </button>
        </div>
      </form>
      <div className="modal-backdrop" onClick={() => !busy && onClose()} aria-hidden="true" />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* One order's payments                                                */
/* ------------------------------------------------------------------ */

function StateBadge({ map, value }) {
  const known = map[value];
  return (
    <span className={`badge badge-sm ${known?.badge ?? "badge-ghost"} whitespace-nowrap`}>
      {known?.label ?? statusLabel(value)}
    </span>
  );
}

function RefundRow({ refund }) {
  return (
    <li className="py-1.5">
      <div className="flex flex-wrap items-center justify-between gap-x-2">
        <span className="font-semibold">{money(refund.amount, refund.currency)}</span>
        <StateBadge map={REFUND_STATUSES} value={refund.status} />
      </div>
      <p className="text-xs opacity-60">
        {formatDateTime(refund.created_at)} · {refund.initiated_by}
      </p>
      {refund.reason && <p className="text-xs opacity-80">“{refund.reason}”</p>}
      {refund.status === "failed" && refund.failure_reason && (
        <p className="text-xs text-error">{refund.failure_reason}</p>
      )}
    </li>
  );
}

/** A provider's own id — long, unbreakable, and exactly what gets pasted
    into the provider's dashboard search, so it is shown whole. */
function ProviderId({ label, value }) {
  if (!value) return null;
  return (
    <p className="text-xs">
      <span className="opacity-60">{label} </span>
      <code className="break-all select-all text-[11px]">{value}</code>
    </p>
  );
}

function TransactionCard({ tx }) {
  const provider = providerLabel(tx.provider);
  const holding = HOLDING_STATES.includes(tx.status);
  return (
    <li className={`rounded-box border border-base-300 p-3 ${holding ? "bg-base-100" : "opacity-70"}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-semibold">
          {provider}
          {tx.method_label && tx.method_label !== provider && (
            <span className="font-normal opacity-70"> · {tx.method_label}</span>
          )}
        </p>
        <StateBadge map={TRANSACTION_STATUSES} value={tx.status} />
      </div>
      <p className="mt-0.5 text-sm">
        {money(tx.amount, tx.currency)}
        {tx.refunded_amount > 0 && (
          <span className="opacity-70"> · {money(tx.refunded_amount, tx.currency)} refunded</span>
        )}
        {holding && tx.refundable_amount > 0 && tx.refunded_amount > 0 && (
          <span className="opacity-70"> · {money(tx.refundable_amount, tx.currency)} still held</span>
        )}
      </p>
      <p className="text-xs opacity-60">
        {tx.paid_at ? `Paid ${formatDateTime(tx.paid_at)}` : `Pay page opened ${formatDateTime(tx.created_at)}`}
      </p>
      <div className="mt-1.5 space-y-0.5">
        <ProviderId label={tx.provider === "paypal" ? "PayPal order" : "Checkout session"} value={tx.provider_reference} />
        <ProviderId label={tx.provider === "paypal" ? "Capture" : "Payment intent"} value={tx.provider_payment_id} />
      </div>
      {tx.failure_reason && <p className="mt-1.5 text-xs text-error">{tx.failure_reason}</p>}
      {tx.dispute_status === "open" && (
        <p className="mt-1.5 flex items-start gap-1.5 text-xs font-semibold text-error">
          <ShieldAlert size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
          Dispute open — answer it in {provider}&rsquo;s dashboard before its deadline.
        </p>
      )}
      {tx.dispute_status === "won" && <p className="mt-1.5 text-xs opacity-70">A dispute on this payment was closed in the shop&rsquo;s favour.</p>}
      {tx.dispute_status === "lost" && <p className="mt-1.5 text-xs opacity-70">A dispute on this payment was lost — the bank took the money back.</p>}
      {tx.refunds.length > 0 && (
        <ul className="mt-2 divide-y divide-base-200 border-t border-dashed border-base-300 pt-1 text-sm">
          {tx.refunds.map((refund) => (
            <RefundRow key={refund.id} refund={refund} />
          ))}
        </ul>
      )}
    </li>
  );
}

/** The payment section of the order drawer. `payments` is the ledger for
    this order (null while loading); `onUpdated(fresh)` hands back a newer
    copy after a refund or a provider check — or null for "reload it". */
export function PaymentsPanel({ order, payments, failed, onRetry, onUpdated }) {
  const toast = useToast();
  const [refunding, setRefunding] = useState(false);
  const [checking, setChecking] = useState(false);
  // What the provider said the last time it was asked — kept on screen,
  // because a toast is gone in four seconds and this is the answer.
  const [providerSaid, setProviderSaid] = useState(null);

  if (failed) return <LoadError message={failed} onRetry={onRetry} />;
  if (!payments) {
    return (
      <p className="flex items-center gap-2 text-sm opacity-60" role="status">
        <span className="loading loading-spinner loading-xs" aria-hidden="true" />
        Reading the payment ledger…
      </p>
    );
  }

  const provider = providerLabel(payments.provider);
  const newestFirst = [...payments.transactions].reverse();

  const checkProvider = async () => {
    if (checking) return;
    setChecking(true);
    try {
      const { data } = await client.post(`/admin/orders/${order.id}/payments/sync`);
      setProviderSaid(data.sync_outcome);
      onUpdated(data);
    } catch (err) {
      toast.error(apiError(err, `${provider} could not be asked`));
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-display font-semibold">Payment</p>
        <div className="flex flex-wrap gap-2">
          {/* Asking only does anything while the order still waits for its
              money — that is exactly when a webhook may have gone missing. */}
          {payments.payment_status === "pending" && (
            <button type="button" className="btn btn-outline btn-xs" onClick={checkProvider} disabled={checking}>
              {checking ? (
                <span className="loading loading-spinner loading-xs" aria-hidden="true" />
              ) : (
                <RefreshCw size={12} aria-hidden="true" />
              )}
              Check with {provider}
            </button>
          )}
          {payments.refundable_amount > 0 && (
            <button type="button" className="btn btn-warning btn-xs" onClick={() => setRefunding(true)}>
              <Undo2 size={12} aria-hidden="true" />
              Refund…
            </button>
          )}
        </div>
      </div>

      {providerSaid && (
        <p className="rounded-box bg-info/10 px-3 py-2 text-sm" role="status">
          {providerSaid}
        </p>
      )}

      {payments.paid_amount > 0 && (
        <dl className="grid grid-cols-3 gap-2 text-center text-sm">
          <div className="rounded-box bg-base-200/60 p-2">
            <dt className="text-xs opacity-60">Paid</dt>
            <dd className="font-bold">{money(payments.paid_amount, payments.currency)}</dd>
          </div>
          <div className="rounded-box bg-base-200/60 p-2">
            <dt className="text-xs opacity-60">Refunded</dt>
            <dd className="font-bold">{money(payments.refunded_amount, payments.currency)}</dd>
          </div>
          <div className="rounded-box bg-base-200/60 p-2">
            <dt className="text-xs opacity-60">Still held</dt>
            <dd className="font-bold">{money(payments.refundable_amount, payments.currency)}</dd>
          </div>
        </dl>
      )}

      {newestFirst.length === 0 ? (
        <p className="text-sm opacity-60">
          No {provider} pay page is on record for this order.
        </p>
      ) : (
        <ul className="space-y-2">
          {newestFirst.map((tx) => (
            <TransactionCard key={tx.id} tx={tx} />
          ))}
        </ul>
      )}

      {payments.events.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-xs font-semibold opacity-70">
            Payment history ({payments.events.length})
          </summary>
          <ol className="mt-2 space-y-1.5 border-l-2 border-dashed border-base-300 pl-3">
            {payments.events.map((event) => (
              <li key={event.id} className="text-xs">
                <span className="font-semibold">{event.event_type}</span>
                {event.to_status && (
                  <span className="opacity-70">
                    {" "}
                    {event.from_status ? `${statusLabel(event.from_status)} → ` : "→ "}
                    {statusLabel(event.to_status)}
                  </span>
                )}
                <span className="opacity-60"> · {formatDateTime(event.created_at)}</span>
                {event.event_id && <span className="opacity-60"> · from {providerLabel(event.provider)}</span>}
                {event.detail && <p className="break-words opacity-70">{event.detail}</p>}
              </li>
            ))}
          </ol>
        </details>
      )}

      {refunding && (
        <RefundDialog
          order={order}
          payments={payments}
          onClose={() => setRefunding(false)}
          onDone={(fresh) => {
            setRefunding(false);
            onUpdated(fresh);
          }}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* The reconciliation report                                           */
/* ------------------------------------------------------------------ */

const ISSUE_TITLES = {
  stuck_pending: "Payment never arrived",
  processing_payment: "Payment still settling",
  duplicate_payment: "Paid twice",
  held_on_cancelled: "Money held for a cancelled order",
  amount_mismatch: "Charged amount differs from the order",
  open_dispute: "Dispute open",
  status_drift: "Order and ledger disagree",
  open_page_on_cancelled: "Pay page open on a cancelled order",
  paid_without_provider: "Marked paid by hand",
  failed_refund: "Refund not sent",
  slow_refund: "Refund slow to confirm",
};

function ProviderCard({ status }) {
  const name = providerLabel(status.provider);
  const rehearsal = status.configured && status.mode !== "live";
  return (
    <div className="rounded-box glass p-4 shadow-sm ring-1 ring-base-300/60">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-display font-semibold">{name}</p>
        {!status.configured ? (
          <span className="badge badge-ghost badge-sm">Not set up</span>
        ) : rehearsal ? (
          <span className="badge badge-warning badge-sm gap-1">
            <FlaskConical size={11} aria-hidden="true" />
            {status.mode === "sandbox" ? "Sandbox" : "Test mode"}
          </span>
        ) : (
          <span className="badge badge-success badge-sm">Live</span>
        )}
      </div>
      <p className="mt-1.5 text-sm opacity-70">
        {!status.configured
          ? "No keys on this deployment — customers are not offered this way to pay."
          : rehearsal
            ? status.provider === "paypal"
              ? "A rehearsal: no real money moves — only PayPal sandbox accounts can pay."
              : "A rehearsal: no real money moves, and real cards are declined."
            : "Taking real payments."}
      </p>
      {status.configured && !status.webhook_ready && (
        <p className="mt-1.5 flex items-start gap-1.5 text-xs font-semibold text-warning">
          <CircleAlert size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
          No webhook configured — payments are only confirmed when the customer comes back to the
          shop, or by the periodic check. Refunds and disputes made at {name} go unseen.
        </p>
      )}
    </div>
  );
}

function IssueRow({ issue, onOpenOrder }) {
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 py-3">
      <div className="min-w-0 flex-1">
        <p className="font-semibold">
          {ISSUE_TITLES[issue.kind] ?? statusLabel(issue.kind)}
          {issue.order_number && <span className="font-normal opacity-70"> · {issue.order_number}</span>}
          {issue.provider && <span className="font-normal opacity-50"> · {providerLabel(issue.provider)}</span>}
        </p>
        <p className="text-sm opacity-80">{issue.detail}</p>
        <p className="text-xs opacity-60">{issue.suggestion}</p>
      </div>
      {issue.order_id && (
        <button
          type="button"
          className="btn btn-outline btn-xs shrink-0"
          onClick={() => onOpenOrder(issue.order_id)}
          aria-label={`Open order ${issue.order_number}`}
        >
          Open order
        </button>
      )}
    </li>
  );
}

/** Everything in the payment book that doesn't add up, with what to do
    about each. Read-only: opening it asks no provider anything. `version`
    changes whenever the desk has acted on an order, so the list re-reads. */
export function ReconciliationReport({ onOpenOrder, version = 0 }) {
  const [report, setReport] = useState(null);
  const [failed, setFailed] = useState(null);
  const [reading, setReading] = useState(false);

  const load = () => {
    setFailed(null);
    setReading(true);
    client
      .get("/admin/payments/reconciliation")
      .then(({ data }) => setReport(data))
      .catch((e) => setFailed(apiError(e, "The payment check didn't load.")))
      .finally(() => setReading(false));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version]);

  if (failed) return <LoadError message={failed} onRetry={load} />;
  if (!report) return <Loading label="Checking the payment book…" />;

  const action = report.issues.filter((issue) => issue.severity === "action");
  const watch = report.issues.filter((issue) => issue.severity !== "action");

  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-2">
        {report.providers.map((status) => (
          <ProviderCard key={status.provider} status={status} />
        ))}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs opacity-60">
          Checked {formatDateTime(report.checked_at)} —
          reads the shop&rsquo;s own ledger; a provider is only asked when you open an order and check it.
        </p>
        <button type="button" className="btn btn-ghost btn-sm" onClick={load} disabled={reading}>
          <RefreshCw size={14} className={reading ? "animate-spin" : ""} aria-hidden="true" />
          Check again
        </button>
      </div>

      {report.issues.length === 0 ? (
        <div className="rounded-box glass flex flex-col items-center gap-2 p-10 text-center shadow-sm ring-1 ring-base-300/60">
          <CircleCheck size={28} className="text-success" aria-hidden="true" />
          <p className="font-display text-lg font-semibold">The payment book adds up</p>
          <p className="max-w-sm text-sm opacity-70">
            Every paid order has its payment, every cancelled one its refund, and nothing is waiting
            on you.
          </p>
        </div>
      ) : (
        <>
          {action.length > 0 && (
            <section className="rounded-box glass p-5 shadow-sm ring-1 ring-error/40">
              <h3 className="font-display flex items-center gap-2 text-lg font-semibold">
                <CircleAlert size={18} className="text-error" aria-hidden="true" />
                Needs you ({action.length})
              </h3>
              <ul className="mt-1 divide-y divide-base-200">
                {action.map((issue, index) => (
                  <IssueRow key={`${issue.kind}-${issue.order_id}-${index}`} issue={issue} onOpenOrder={onOpenOrder} />
                ))}
              </ul>
            </section>
          )}
          {watch.length > 0 && (
            <section className="rounded-box glass p-5 shadow-sm ring-1 ring-base-300/60">
              <h3 className="font-display text-lg font-semibold">Worth a look ({watch.length})</h3>
              <ul className="mt-1 divide-y divide-base-200">
                {watch.map((issue, index) => (
                  <IssueRow key={`${issue.kind}-${issue.order_id}-${index}`} issue={issue} onOpenOrder={onOpenOrder} />
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}
