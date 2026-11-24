import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import {
  BadgeCheck,
  ChevronRight,
  Copy,
  Crown,
  Download,
  Gift,
  Heart,
  Hourglass,
  KeyRound,
  Mail,
  MailCheck,
  MailX,
  Phone,
  Search,
  SlidersHorizontal,
  UsersRound,
  X,
} from "lucide-react";
import client, { apiError } from "../../api/client";
import { formatDate, formatDateTime, hasNonActiveCurrency, money, sinceLabel } from "../../lib/format";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";
import { LoadError } from "../../components/Loading";
import Pager from "../../components/Pager";
import Drawer from "../../components/Drawer";
import OrderStatusBadge from "../../components/OrderStatusBadge";

/* What each tier wears. The rules live on the server (app/loyalty.py) —
   this is only their wardrobe. */
const TIERS = {
  vip: { label: "VIP", Icon: Crown, badge: "badge-warning gap-1 text-warning-content" },
  loyal: { label: "Loyal", Icon: Heart, badge: "badge-secondary gap-1 text-secondary-content" },
  lapsed: { label: "Lapsed", Icon: Hourglass, badge: "badge-ghost gap-1 opacity-75" },
  regular: { label: "Regular", Icon: null, badge: "badge-ghost opacity-75" },
};

const TIER_FILTERS = [
  { value: "all", label: "Everyone" },
  { value: "vip", label: "VIP" },
  { value: "loyal", label: "Loyal" },
  { value: "lapsed", label: "Lapsed" },
];

// `phrase` is how the roll's count line says the order it is in.
const SORTS = [
  { value: "spend", label: "Top spend", phrase: "top spend first" },
  { value: "orders", label: "Most orders", phrase: "most orders first" },
  { value: "last", label: "Last order", phrase: "latest order first" },
  { value: "joined", label: "Newest members", phrase: "newest members first" },
  { value: "name", label: "Name A–Z", phrase: "by name, A to Z" },
];

/* The desk's manual word for a customer — same vocabulary as the measured
   tiers, but a different thing entirely: this one is the baker's own call,
   written by hand, and nothing ever recalculates it. */
const CLASSIFICATIONS = ["vip", "loyal", "lapsed", "regular"];

/* Everything the roll can be narrowed by besides the tier chips — one quiet
   panel, opened on request, each choice also shown as a chip to undo. */
const MORE_FILTERS = [
  {
    key: "classification",
    label: "Desk's word",
    options: [...CLASSIFICATIONS.map((value) => ({ value, label: TIERS[value].label })), { value: "none", label: "No word yet" }],
  },
  {
    key: "contact",
    label: "Signs in with",
    options: [
      { value: "email", label: "Email" },
      { value: "phone", label: "Phone only" },
    ],
  },
  {
    key: "consent",
    label: "Marketing",
    options: [
      { value: "yes", label: "Can be emailed" },
      { value: "no", label: "No consent" },
    ],
  },
  {
    key: "status",
    label: "Account",
    options: [
      { value: "active", label: "Active" },
      { value: "inactive", label: "Deactivated" },
    ],
  },
  {
    key: "role",
    label: "Role",
    options: [
      { value: "customer", label: "Customers" },
      { value: "admin", label: "Admins" },
    ],
  },
];

/* The tiers that have earned a code of their own — the loyalty the shop
   thanks with a voucher. */
const VOUCHER_TIERS = ["vip", "loyal"];

const PER_PAGE = 15;

// The desk's panels, as on the dashboard and the order rail.
const PANEL = "glass rounded-box p-5 shadow-sm ring-1 ring-base-300/60 sm:p-6";
const RAIL = "glass overflow-hidden rounded-box shadow-sm ring-1 ring-base-300/60";

const fullName = (u) => `${u.first_name} ${u.last_name}`.trim();
const initials = (u) => `${u.first_name?.[0] ?? ""}${u.last_name?.[0] ?? ""}`.toUpperCase() || "?";
const ordersLabel = (n) => `${n} ${n === 1 ? "order" : "orders"}`;
const share = (n, of) => (of > 0 ? `${Math.round((n / of) * 100)}%` : "—");

function TierBadge({ loyalty, quiet = false }) {
  // In the roll a Regular says nothing worth a pill; on the card it does.
  if (quiet && (loyalty ?? "regular") === "regular") return null;
  const tier = TIERS[loyalty] ?? TIERS.regular;
  const Icon = tier.Icon;
  return (
    <span className={`badge badge-sm border ${tier.badge}`}>
      {Icon && <Icon size={11} aria-hidden="true" />}
      {tier.label}
    </span>
  );
}

function Avatar({ user, size = "size-10 text-sm" }) {
  return user.avatar_url ? (
    <img src={user.avatar_url} alt="" className={`${size} shrink-0 rounded-full object-cover ring-1 ring-base-300`} />
  ) : (
    <span
      className={`${size} flex shrink-0 items-center justify-center rounded-full bg-secondary/15 font-bold text-secondary`}
      aria-hidden="true"
    >
      {initials(user)}
    </span>
  );
}

/** Email first, else the phone the account signs in with. If an address has
    to wrap on a phone it wraps after the @, not through "example.co / m". */
function Contact({ user }) {
  if (user.email) {
    const [local, ...rest] = user.email.split("@");
    return (
      <>
        {local}@<wbr />
        {rest.join("@")}
      </>
    );
  }
  return <>{user.phone ?? "No contact on file"} · no email</>;
}

/* ------------------------------------------------------------------ */
/* The roll at a glance                                                */
/* ------------------------------------------------------------------ */

function Figure({ label, value, note }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs opacity-70">{label}</dt>
      <dd className="figures mt-1 text-xl font-semibold tracking-tight [overflow-wrap:anywhere] sm:text-2xl">{value}</dd>
      <dd className="mt-1 text-xs opacity-75">{note}</dd>
    </div>
  );
}

function Overview({ summary }) {
  return (
    <section className={`${PANEL} mb-6`} aria-label="Customers at a glance">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-5 lg:grid-cols-4">
        <Figure
          label="Customers"
          value={summary.customers}
          note={summary.new_last_30_days > 0 ? `+${summary.new_last_30_days} in the last 30 days` : "None new in the last 30 days"}
        />
        <Figure
          label="Have ordered"
          value={summary.ordered}
          note={`${share(summary.ordered, summary.customers)} of customers · ${summary.returning} came back`}
        />
        <Figure
          label="Lifetime spend"
          value={money(summary.lifetime_spend)}
          note={summary.ordered > 0 ? `${money(summary.average_spend)} per customer who ordered` : "No orders yet"}
        />
        <Figure
          label="Can be emailed"
          value={summary.marketing_consent}
          note={`${share(summary.marketing_consent, summary.customers)} gave consent · ${summary.phone_only} phone-only`}
        />
      </dl>
    </section>
  );
}

function GlanceSkeleton() {
  return (
    <div className={`${PANEL} mb-6`} aria-hidden="true">
      <div className="grid grid-cols-2 gap-x-6 gap-y-5 lg:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="space-y-2">
            <span className="block h-3 w-20 animate-pulse rounded bg-base-200" />
            <span className="block h-7 w-16 animate-pulse rounded bg-base-200" />
            <span className="block h-3 w-28 animate-pulse rounded bg-base-200" />
          </div>
        ))}
      </div>
    </div>
  );
}

/** The roll's shape while it loads — rows the size of the real ones, so
    nothing jumps when they land. The words are for a screen reader. */
function RollSkeleton() {
  return (
    <div className={RAIL} role="status">
      <span className="sr-only">Rolling out the regulars…</span>
      <ul className="divide-y divide-base-300/60" aria-hidden="true">
        {Array.from({ length: 6 }, (_, i) => (
          <li key={i} className="flex items-center gap-4 px-4 py-4 sm:px-5">
            <span className="size-10 shrink-0 animate-pulse rounded-full bg-base-200" />
            <span className="h-4 min-w-0 flex-1 animate-pulse rounded bg-base-200" />
            <span className="h-4 w-16 animate-pulse rounded bg-base-200" />
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* One customer's card                                                 */
/* ------------------------------------------------------------------ */

function Section({ title, children, aside = null }) {
  return (
    <section>
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="font-display text-sm font-bold tracking-widest uppercase opacity-65">{title}</h3>
        {aside}
      </div>
      <div className="mt-2">{children}</div>
    </section>
  );
}

function CopyButton({ text, label }) {
  const toast = useToast();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`${label} copied`);
    } catch {
      toast.info("Select it and copy it by hand");
    }
  };
  return (
    <button type="button" className="btn btn-ghost btn-xs btn-square" onClick={copy} aria-label={`Copy ${label.toLowerCase()}`} title="Copy">
      <Copy size={13} aria-hidden="true" />
    </button>
  );
}

/* Everything about one person in one place, so the roll itself stays a
   roll: who they are and how to reach them, what the order book says, the
   desk's word and its ledger, their marketing permission and the rare
   account knobs. Consent is recorded, never assumed — a grant must say
   where it was given and carry evidence the owner could show a regulator. */
function CustomerDrawer({ user, voucher, onClose, onChanged, onUpdate, onWord }) {
  const toast = useToast();
  const { user: me } = useAuth();
  const [ledger, setLedger] = useState(null);
  const [orders, setOrders] = useState(null); // { orders, pagination } | "failed"
  // The roll carries only the timestamp; any write here brings back the
  // full state (source, evidence, suppression) straight from the server.
  const [consent, setConsent] = useState({
    marketing_consent_at: user.marketing_consent_at,
    marketing_consent_source: null,
    marketing_consent_evidence: null,
    suppressed: false,
    suppression_reason: null,
  });
  const [proof, setProof] = useState({ source: "", evidence: "" });
  const [busy, setBusy] = useState(false);
  // A password reset link made here, for the baker to pass on.
  const [resetLink, setResetLink] = useState(null); // { link, expires_minutes }
  const [linking, setLinking] = useState(false);
  const self = user.id === me.id;
  // What the desk changes here is kept here too: shown at once, put back if
  // the server says no. The roll behind may filter this customer out after
  // a change, and the card must not snap back to the old answer.
  const [word, setWord] = useState(user.classification ?? "");
  const [account, setAccount] = useState({ role: user.role, is_active: user.is_active });

  const loadLedger = () =>
    client
      .get(`/admin/users/${user.id}/classification-history`)
      .then(({ data }) => setLedger(data))
      .catch((err) => toast.error(apiError(err)));

  const changeWord = async (value) => {
    const before = word;
    setWord(value);
    if (await onWord(user, value)) loadLedger();
    else setWord(before);
  };

  const changeAccount = async (payload) => {
    const before = account;
    setAccount((a) => ({ ...a, ...payload }));
    if (!(await onUpdate(user.id, payload))) setAccount(before);
  };

  useEffect(() => {
    loadLedger();
    // Their own orders exactly — newest first, the last five.
    client
      .get("/admin/orders", { params: { user_id: user.id, per_page: 5 } })
      .then(({ data }) => setOrders(data))
      .catch(() => setOrders("failed"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user.id]);

  const makeResetLink = async () => {
    setLinking(true);
    try {
      const { data } = await client.post(`/admin/users/${user.id}/reset-link`);
      setResetLink(data);
    } catch (err) {
      toast.error(apiError(err, "Couldn't make the link"));
    } finally {
      setLinking(false);
    }
  };

  const copyResetLink = async () => {
    try {
      await navigator.clipboard.writeText(resetLink.link);
      toast.success("Link copied — send it to the customer");
    } catch {
      toast.info("Select the link and copy it by hand");
    }
  };

  const recordConsent = async (granted) => {
    setBusy(true);
    try {
      const { data } = await client.put(`/admin/users/${user.id}/marketing-consent`, {
        granted,
        ...(granted ? { source: proof.source, evidence: proof.evidence } : {}),
      });
      setConsent(data);
      setProof({ source: "", evidence: "" });
      toast.success(granted ? "Consent recorded" : "Consent withdrawn — the address is suppressed");
      onChanged();
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setBusy(false);
    }
  };

  const copyCode = async () => {
    try {
      await navigator.clipboard.writeText(voucher.code);
      toast.success(`${voucher.code} copied — ${voucher.percent_off}% off for ${user.first_name}`);
    } catch {
      toast.error("Couldn't reach the clipboard");
    }
  };

  const average = user.order_count > 0 ? user.lifetime_spend / user.order_count : 0;
  const allOrders = `/admin/orders?search=${encodeURIComponent(user.email ?? user.phone ?? user.first_name)}`;

  return (
    <Drawer
      title={fullName(user)}
      subtitle={`${account.role === "admin" ? "Admin" : "Customer"} since ${formatDate(user.created_at)}`}
      label={`Customer card for ${fullName(user)}`}
      onClose={onClose}
      busy={busy}
    >
      <div className="flex-1 space-y-6 overflow-y-auto px-6 py-5">
        {/* Who, and how to reach them */}
        <section className="flex items-start gap-4">
          <Avatar user={user} size="size-14 text-lg" />
          <div className="min-w-0 flex-1 space-y-1.5">
            <div className="flex flex-wrap items-center gap-1.5">
              <TierBadge loyalty={user.loyalty} />
              {account.role === "admin" && <span className="badge badge-sm badge-neutral">Admin</span>}
              {!account.is_active && <span className="badge badge-sm badge-outline">Deactivated</span>}
              {self && <span className="badge badge-sm badge-ghost">You</span>}
            </div>
            {user.email ? (
              <p className="flex items-center gap-1 text-sm">
                <Mail size={14} className="shrink-0 opacity-60" aria-hidden="true" />
                <a href={`mailto:${user.email}`} className="link min-w-0 [overflow-wrap:anywhere]">
                  {user.email}
                </a>
                <CopyButton text={user.email} label="Email" />
              </p>
            ) : (
              <p className="text-xs opacity-70">No email — signs in with their phone number.</p>
            )}
            {user.phone && (
              <p className="flex flex-wrap items-center gap-1 text-sm">
                <Phone size={14} className="shrink-0 opacity-60" aria-hidden="true" />
                <a href={`tel:${user.phone}`} className="link">
                  {user.phone}
                </a>
                <CopyButton text={user.phone} label="Phone number" />
                {user.phone_verified && (
                  <span className="inline-flex items-center gap-1 text-xs text-success">
                    <BadgeCheck size={13} aria-hidden="true" />
                    confirmed for text sign-in
                  </span>
                )}
              </p>
            )}
          </div>
        </section>

        {/* What the order book says */}
        <dl className="grid grid-cols-2 gap-3">
          {[
            ["Orders", user.order_count],
            ["Spent", user.order_count > 0 ? money(user.lifetime_spend) : "—"],
            ["Average order", user.order_count > 0 ? money(average) : "—"],
            ["Last order", user.last_order_at ? sinceLabel(user.last_order_at) : "—"],
          ].map(([label, value]) => (
            <div key={label} className="rounded-box border border-base-300/70 bg-base-200/40 px-4 py-3">
              <dt className="text-xs opacity-70">{label}</dt>
              <dd className="figures mt-0.5 text-lg font-semibold">{value}</dd>
            </div>
          ))}
        </dl>
        {user.order_count > 0 && hasNonActiveCurrency(Object.keys(user.lifetime_spend_by_currency ?? {})) && (
          <p className="-mt-3 text-xs opacity-70">
            By currency:{" "}
            {Object.entries(user.lifetime_spend_by_currency)
              .map(([code, amount]) => money(amount, code))
              .join(" · ")}
          </p>
        )}

        <Section
          title="Recent orders"
          aside={
            // The order rail's search matches by "contains", so it can find a
            // few more than this card does — the link promises no number.
            orders && orders !== "failed" && orders.pagination.total_items > 0 ? (
              <Link to={allOrders} className="link link-secondary text-xs font-semibold">
                See all in Orders
              </Link>
            ) : null
          }
        >
          {orders === null ? (
            <div className="space-y-2" aria-label="Loading their orders">
              <div className="skeleton h-4 w-full" />
              <div className="skeleton h-4 w-2/3" />
            </div>
          ) : orders === "failed" ? (
            <p className="text-xs opacity-70">Their orders didn&rsquo;t load — close the card and open it again.</p>
          ) : orders.orders.length === 0 ? (
            <p className="text-xs opacity-70">No orders yet.</p>
          ) : (
            <>
            <ul className="divide-y divide-base-300/60 rounded-box border border-base-300/70">
              {orders.orders.map((o) => (
                <li key={o.id} className="px-3 py-2.5">
                  {/* The number and the amount on one line, like a receipt;
                      the status and the day under them — so a long status
                      never cuts the number short on a narrow card. */}
                  <div className="flex items-baseline justify-between gap-3">
                    <Link
                      to={`/admin/orders?search=${encodeURIComponent(o.order_number)}`}
                      className="figures min-w-0 truncate text-sm font-semibold hover:text-secondary"
                    >
                      {o.order_number}
                    </Link>
                    <span className="figures shrink-0 text-sm font-semibold">{money(o.total, o.currency)}</span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                    <OrderStatusBadge status={o.status} className="badge-sm" />
                    <span className="text-xs opacity-70">{formatDate(o.created_at)}</span>
                  </div>
                </li>
              ))}
            </ul>
            {orders.pagination.total_items > orders.orders.length && (
              // Every order this account placed — cancelled ones too, which
              // the "Orders" figure above leaves out.
              <p className="mt-1.5 text-xs opacity-70">
                The latest {orders.orders.length} of {orders.pagination.total_items}, cancelled ones included.
              </p>
            )}
            </>
          )}
        </Section>

        {/* The desk's own word — beside the measured tier, never derived from
            it — and every change to it, newest first. */}
        <Section title="Loyalty">
          <div className="space-y-3 rounded-box border border-base-300 bg-base-200/50 p-4">
            <p className="flex flex-wrap items-center gap-2 text-sm">
              <span className="opacity-70">Measured tier</span>
              <TierBadge loyalty={user.loyalty} />
              {voucher && (
                <button
                  type="button"
                  className="btn btn-ghost btn-xs border border-base-300 font-mono"
                  onClick={copyCode}
                  aria-label={`Copy the ${TIERS[user.loyalty].label} code for ${user.first_name}`}
                >
                  <Gift size={13} aria-hidden="true" />
                  {voucher.code} · {voucher.percent_off}%
                </button>
              )}
            </p>
            <div>
              <label className="label text-xs" htmlFor={`word-${user.id}`}>
                Desk&rsquo;s word
              </label>
              <select
                id={`word-${user.id}`}
                className="select select-bordered select-sm w-full max-w-52"
                value={word}
                onChange={(e) => changeWord(e.target.value)}
              >
                <option value="">— none —</option>
                {CLASSIFICATIONS.map((c) => (
                  <option key={c} value={c}>
                    {TIERS[c].label}
                  </option>
                ))}
              </select>
            </div>
            {ledger === null ? (
              <div className="space-y-2" aria-label="Loading the ledger">
                <div className="skeleton h-4 w-full" />
              </div>
            ) : ledger.length === 0 ? (
              <p className="text-xs opacity-70">No word set yet.</p>
            ) : (
              <ul className="space-y-1.5">
                {ledger.map((event) => (
                  <li key={event.id} className="text-xs">
                    <span className="font-semibold">{event.from_classification ? TIERS[event.from_classification].label : "—"}</span>
                    {" → "}
                    <span className="font-semibold text-secondary">
                      {event.to_classification ? TIERS[event.to_classification].label : "—"}
                    </span>
                    <span className="opacity-70">
                      {" "}
                      · {event.admin_name} · {formatDateTime(event.created_at)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Section>

        {/* Marketing permission — the gate every campaign and voucher email passes */}
        <Section title="Marketing permission">
          {consent.marketing_consent_at ? (
            <div className="rounded-box border border-success/30 bg-success/10 p-4 text-sm">
              <p className="flex items-center gap-2 font-semibold text-success">
                <MailCheck size={16} aria-hidden="true" />
                Consented — news may be sent
              </p>
              <p className="mt-1 text-xs opacity-75">
                Recorded {formatDateTime(consent.marketing_consent_at)}
                {consent.marketing_consent_source && <> · source: {consent.marketing_consent_source}</>}
              </p>
              {consent.suppressed && (
                <p className="mt-2 text-xs font-semibold text-error">
                  But the address is on the suppression list ({consent.suppression_reason}) — a fresh
                  recorded consent is the only thing that lifts it.
                </p>
              )}
              <button
                type="button"
                className="btn btn-ghost btn-xs mt-3 text-error"
                disabled={busy}
                onClick={() => recordConsent(false)}
              >
                <MailX size={14} /> Withdraw consent
              </button>
            </div>
          ) : !user.email ? (
            // Consent is to being emailed, and there is no address to
            // email — the server refuses to record one, so no form is
            // offered that could only fail.
            <p className="rounded-box border border-base-300 bg-base-200/50 p-4 text-xs opacity-80">
              This customer signs in by phone and has no email address, so there is nothing marketing
              could be sent to. Once they add an email in their profile, consent can be recorded here.
            </p>
          ) : (
            <form
              className="space-y-3 rounded-box border border-base-300 bg-base-200/50 p-4"
              onSubmit={(e) => {
                e.preventDefault();
                recordConsent(true);
              }}
            >
              <p className="text-xs opacity-70">
                No recorded consent — nothing marketing may be emailed. An account, an order or a
                newsletter signup is <span className="font-semibold">not</span> consent by itself:
                record what the customer actually agreed to.
              </p>
              <div>
                <label className="label text-xs" htmlFor="mk-source">Where it was given</label>
                <input
                  id="mk-source"
                  className="input input-bordered input-sm w-full"
                  required
                  maxLength={50}
                  placeholder="at the counter, phone call, signup form…"
                  value={proof.source}
                  onChange={(e) => setProof((p) => ({ ...p, source: e.target.value }))}
                />
              </div>
              <div>
                <label className="label text-xs" htmlFor="mk-evidence">Evidence (what a regulator could be shown)</label>
                <textarea
                  id="mk-evidence"
                  className="textarea textarea-bordered textarea-sm w-full"
                  required
                  maxLength={1000}
                  rows={2}
                  placeholder="Signed card on file, email dated …, ticked box on the paper form…"
                  value={proof.evidence}
                  onChange={(e) => setProof((p) => ({ ...p, evidence: e.target.value }))}
                />
              </div>
              <button type="submit" className="btn btn-secondary btn-sm text-secondary-content" disabled={busy}>
                {busy ? "Recording…" : "Record consent"}
              </button>
            </form>
          )}
        </Section>

        {/* Account — the rare knobs */}
        <Section title="Account">
          <div className="flex flex-wrap items-end gap-5 rounded-box border border-base-300 bg-base-200/50 p-4">
            <div>
              <label className="label text-xs" htmlFor={`role-${user.id}`}>Role</label>
              <select
                id={`role-${user.id}`}
                className="select select-bordered select-sm"
                value={account.role}
                onChange={(e) => changeAccount({ role: e.target.value })}
              >
                <option value="customer">customer</option>
                <option value="admin">admin</option>
              </select>
            </div>
            <label className="flex cursor-pointer items-center gap-2 pb-1.5 text-sm">
              <input
                type="checkbox"
                className="toggle toggle-success toggle-sm"
                checked={account.is_active}
                disabled={self}
                onChange={(e) => changeAccount({ is_active: e.target.checked })}
              />
              Active
            </label>
            {self && (
              <p className="w-full text-xs opacity-70">
                This is your own account — the shop never lets the last admin step down from here.
              </p>
            )}
          </div>

          {/* Getting a customer back in when "Forgot password" can't: an
              account with no email, or mail that never arrives. The link
              is made here and passed on by the baker. */}
          {account.role !== "admin" && account.is_active && (
            <div className="mt-3 rounded-box border border-base-300 bg-base-200/50 p-4">
              <p className="text-sm font-semibold">Forgotten password</p>
              <p className="mt-0.5 text-xs opacity-70">
                {user.email
                  ? "They can ask for a link themselves from the log-in page. If the mail doesn't reach them, make one here and send it yourself."
                  : "This account has no email, so a link can't be mailed. Make one here and send it yourself — by message, or read it out."}
              </p>
              {resetLink ? (
                <div className="mt-3">
                  <label className="label text-xs" htmlFor={`reset-link-${user.id}`}>
                    Reset link — works once, for {resetLink.expires_minutes} minutes
                  </label>
                  <div className="join w-full">
                    <input
                      id={`reset-link-${user.id}`}
                      className="input input-bordered input-sm join-item w-full font-mono text-xs"
                      readOnly
                      value={resetLink.link}
                      onFocus={(e) => e.target.select()}
                    />
                    <button type="button" className="btn btn-secondary btn-sm join-item text-secondary-content" onClick={copyResetLink}>
                      <Copy size={14} aria-hidden="true" />
                      Copy
                    </button>
                  </div>
                  <p className="mt-1.5 text-xs opacity-70">
                    Whoever opens it can set this account&rsquo;s password — send it only to the customer.
                  </p>
                </div>
              ) : (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm mt-3 border border-base-300"
                  onClick={makeResetLink}
                  disabled={linking}
                >
                  {linking ? (
                    <span className="loading loading-spinner loading-xs" aria-hidden="true" />
                  ) : (
                    <KeyRound size={15} aria-hidden="true" />
                  )}
                  Make a reset link
                </button>
              )}
            </div>
          )}
        </Section>
      </div>
    </Drawer>
  );
}

/* ------------------------------------------------------------------ */
/* The roll                                                            */
/* ------------------------------------------------------------------ */

export default function AdminUsers() {
  const toast = useToast();
  const { user: me } = useAuth();
  const [params, setParams] = useSearchParams();
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(null);
  const [search, setSearch] = useState(params.get("search") ?? "");
  const [vouchers, setVouchers] = useState(null); // { vip: coupon|null, loyal: coupon|null }
  const [coupons, setCoupons] = useState([]);
  const [card, setCard] = useState(null); // the customer whose card is open
  const [codesOpen, setCodesOpen] = useState(false); // the loyalty-code pickers, on request
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [exporting, setExporting] = useState(false);

  // Everything the roll shows is read from the address bar, so a narrowed
  // roll survives a reload and the back button, and can be shared.
  const pick = (key, allowed, fallback = "") => (allowed.includes(params.get(key)) ? params.get(key) : fallback);
  const tier = pick("tier", ["vip", "loyal", "lapsed"], "all");
  const filters = Object.fromEntries(MORE_FILTERS.map((f) => [f.key, pick(f.key, f.options.map((o) => o.value))]));
  const sort = pick("sort", SORTS.map((s) => s.value), "spend");
  const searched = params.get("search") ?? "";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const narrowing = {
    tier: tier === "all" ? undefined : tier,
    ...Object.fromEntries(Object.entries(filters).map(([key, value]) => [key, value || undefined])),
    search: searched || undefined,
    sort,
  };
  const activeFilters = MORE_FILTERS.filter((f) => filters[f.key]).map((f) => ({
    key: f.key,
    label: f.label,
    value: f.options.find((o) => o.value === filters[f.key]).label,
  }));
  const filtered = Boolean(searched) || tier !== "all" || activeFilters.length > 0;

  // The shelf is served ready-cut by the desk: narrowed, searched, ordered,
  // one page at a time — the tier counts and the summary ride along so the
  // chips and the figures count the whole roll from any page.
  const load = () =>
    client
      .get("/admin/users", { params: { ...narrowing, page, per_page: PER_PAGE } })
      .then(({ data }) => {
        // A page past the end — the last one emptied by a change made here,
        // or an edited address: on to the last page that has people on it.
        const { total_items: count, total_pages: pages } = data.pagination;
        if (data.items.length === 0 && count > 0 && page > 1) {
          setParam("page", pages > 1 ? String(pages) : "");
          return;
        }
        setData(data);
        setFailed(null);
        // Any write re-fetches the roll; the open card follows its fresh row
        // (figures, consent) and keeps what it changed itself.
        setCard((open) => (open === null ? null : (data.items.find((u) => u.id === open.id) ?? open)));
      })
      .catch((err) => {
        // Said twice on purpose: the toast for a refresh that failed behind
        // an open roll, the page itself when there is no roll to show.
        setFailed(apiError(err, "The customer roll didn't load."));
        toast.error(apiError(err));
      });

  const fetchRoll = () => {
    setData(null);
    setFailed(null);
    load();
  };

  const paramKey = params.toString();
  useEffect(() => {
    fetchRoll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paramKey]);

  // Which coupon each tier hands out, plus the tin to choose from — once
  // per visit, not per shelf.
  useEffect(() => {
    Promise.all([client.get("/admin/loyalty-vouchers"), client.get("/admin/coupons")])
      .then(([v, c]) => {
        setVouchers(v.data);
        // A tier's code is handed to every regular in that tier, so only
        // shop-wide codes qualify — a voucher minted for one customer
        // would be refused at checkout for everyone else.
        setCoupons(c.data.filter((coupon) => coupon.restricted_to_user_id == null));
      })
      .catch((err) => toast.error(apiError(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A new narrowing means starting from its first page.
  const setParam = (key, value) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.delete("page");
    setParams(next);
  };

  const clearAll = () => {
    setSearch("");
    setParams(sort === "spend" ? {} : { sort });
  };

  const submitSearch = (e) => {
    e.preventDefault();
    setParam("search", search.trim());
    setSearch(search.trim());
  };

  // Both writes answer the card with a yes or a no, so it can keep or put
  // back what it already shows.
  const update = async (id, payload) => {
    try {
      await client.put(`/admin/users/${id}`, payload);
      toast.success("User updated");
      await load();
      return true;
    } catch (err) {
      toast.error(apiError(err));
      return false;
    }
  };

  // The desk's own word for a customer — a different thing from the measured
  // tier beside it: hand-set, hand-cleared, and every change lands in the
  // ledger the card shows.
  const saveWord = async (user, value) => {
    try {
      await client.put(`/admin/users/${user.id}/classification`, { classification: value || null });
      toast.success(
        value ? `${user.first_name} marked ${TIERS[value].label} at the desk` : `Desk's word cleared for ${user.first_name}`,
      );
      await load();
      return true;
    } catch (err) {
      toast.error(apiError(err));
      return false;
    }
  };

  const copyCode = async (coupon) => {
    if (!coupon) return;
    try {
      await navigator.clipboard.writeText(coupon.code);
      toast.success(`${coupon.code} copied — ${coupon.percent_off}% off`);
    } catch {
      toast.error("Couldn't reach the clipboard");
    }
  };

  const linkVoucher = async (tierKey, couponId) => {
    try {
      const { data: next } = await client.put(`/admin/loyalty-vouchers/${tierKey}`, { coupon_id: couponId });
      setVouchers(next);
      toast.success(
        couponId
          ? `${next[tierKey]?.code ?? "The code"} is now the ${TIERS[tierKey].label} code`
          : `${TIERS[tierKey].label} code cleared`,
      );
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  // What is exported is what the desk is looking at — every page of it.
  const exportCsv = async () => {
    setExporting(true);
    try {
      const res = await client.get("/admin/users/export", { params: narrowing, responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      const link = document.createElement("a");
      link.href = url;
      link.download = "bakehouse-customers.csv";
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(apiError(err, "Export failed"));
    } finally {
      setExporting(false);
    }
  };

  if (data === null && failed) return <LoadError message={failed} onRetry={fetchRoll} />;

  const tierCounts = data?.tier_counts ?? {};
  const everyone = Object.values(tierCounts).reduce((sum, n) => sum + n, 0);
  const worthAVoucher = (tierCounts.vip ?? 0) + (tierCounts.loyal ?? 0);
  const users = data?.items ?? [];
  const total = data?.pagination.total_items ?? 0;
  const sortPhrase = SORTS.find((s) => s.value === sort).phrase;

  return (
    <div>
      {/* The page's own name under the desk's, with its one export */}
      <div className="mb-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div>
          <h2 className="font-display text-2xl font-semibold sm:text-3xl">Users</h2>
          {data && (
            <p className="mt-1 text-sm opacity-70">
              {data.summary.customers} {data.summary.customers === 1 ? "customer" : "customers"} ·{" "}
              {data.summary.admins} {data.summary.admins === 1 ? "admin" : "admins"}
              {data.summary.deactivated > 0 && ` · ${data.summary.deactivated} deactivated`}
            </p>
          )}
        </div>
        {/* The word from a tablet up; on a phone the icon alone, still named. */}
        <button
          type="button"
          className="btn btn-ghost border border-base-300"
          onClick={exportCsv}
          disabled={exporting || !data}
          aria-label="Export CSV"
          title="Export CSV — every row of this view"
        >
          {exporting ? (
            <span className="loading loading-spinner loading-xs" aria-hidden="true" />
          ) : (
            <Download size={16} aria-hidden="true" />
          )}
          <span className="hidden sm:inline">Export CSV</span>
        </button>
      </div>

      {data ? <Overview summary={data.summary} /> : <GlanceSkeleton />}

      {/* The codes loyalty earns — one quiet line; the pickers stay folded
          away until asked for, because this is shop config, not roll work */}
      {worthAVoucher > 0 && (
        <div className="mb-6 rounded-box glass px-4 py-2.5 text-sm shadow-sm ring-1 ring-base-300/60">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <span className="text-xs font-semibold tracking-widest uppercase opacity-65">Loyalty codes</span>
            {VOUCHER_TIERS.map((key) => {
              const t = TIERS[key];
              const coupon = vouchers?.[key];
              const Icon = t.Icon;
              return (
                <span key={key} className="flex items-center gap-1.5">
                  <Icon size={14} className="text-secondary" aria-hidden="true" />
                  <span className="font-semibold">{t.label}</span>
                  {coupon ? (
                    <button
                      type="button"
                      className="btn btn-ghost btn-xs px-1.5 font-mono text-xs font-bold"
                      onClick={() => copyCode(coupon)}
                      aria-label={`Copy the ${t.label} code`}
                      title={`Copy ${coupon.code}`}
                    >
                      {coupon.code} · {coupon.percent_off}%
                    </button>
                  ) : (
                    <span className="opacity-70">no code yet</span>
                  )}
                </span>
              );
            })}
            <button
              type="button"
              className="btn btn-ghost btn-xs ml-auto"
              onClick={() => setCodesOpen((o) => !o)}
              aria-expanded={codesOpen}
            >
              {codesOpen ? "Done" : "Change"}
            </button>
          </div>
          {codesOpen && (
            <div className="mt-2 flex flex-wrap gap-x-6 gap-y-2 border-t border-base-300/60 pt-2">
              {VOUCHER_TIERS.map((key) => {
                const coupon = vouchers?.[key];
                return (
                  <label key={key} className="flex items-center gap-2 text-xs">
                    <span className="font-semibold">{TIERS[key].label} code</span>
                    <select
                      className="select select-bordered select-xs max-w-44"
                      value={coupon?.id ?? ""}
                      onChange={(e) => linkVoucher(key, e.target.value ? Number(e.target.value) : null)}
                      aria-label={`Coupon for the ${TIERS[key].label} tier`}
                    >
                      <option value="">— none —</option>
                      {coupons.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.code} · {c.percent_off}%{c.is_active ? "" : " (paused)"}
                        </option>
                      ))}
                    </select>
                  </label>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Find, narrow, order. The search takes the room; filters and the
          sort sit beside it, and wrap under it on a phone. */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <form onSubmit={submitSearch} className="join min-w-0 flex-1 basis-64" role="search">
          <label className="input input-bordered join-item flex min-w-0 flex-1 items-center gap-2">
            <Search size={16} className="shrink-0 opacity-60" aria-hidden="true" />
            <input
              type="search"
              className="min-w-0 grow"
              placeholder="Name, email, phone"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Search users"
            />
          </label>
          <button type="submit" className="btn btn-primary join-item">
            Search
          </button>
        </form>
        <button
          type="button"
          className={`btn btn-ghost border border-base-300 ${filtersOpen ? "bg-base-200" : ""}`}
          onClick={() => setFiltersOpen((open) => !open)}
          aria-expanded={filtersOpen}
          aria-controls="user-filters"
        >
          <SlidersHorizontal size={16} aria-hidden="true" />
          Filters
          {activeFilters.length > 0 && (
            <span className="badge badge-sm badge-secondary text-secondary-content">{activeFilters.length}</span>
          )}
        </button>
        <select
          className="select select-bordered w-auto"
          value={sort}
          onChange={(e) => setParam("sort", e.target.value === "spend" ? "" : e.target.value)}
          aria-label="Sort users"
        >
          {SORTS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      </div>

      {filtersOpen && (
        <div id="user-filters" className={`${PANEL} mb-3 grid grid-cols-1 gap-4 min-[420px]:grid-cols-2 lg:grid-cols-5`}>
          {MORE_FILTERS.map((f) => (
            <div key={f.key} className="min-w-0">
              <label className="label text-xs font-semibold" htmlFor={`uf-${f.key}`}>
                {f.label}
              </label>
              <select
                id={`uf-${f.key}`}
                className="select select-bordered select-sm w-full"
                value={filters[f.key]}
                onChange={(e) => setParam(f.key, e.target.value)}
              >
                <option value="">Any</option>
                {f.options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
          ))}
        </div>
      )}

      {/* The tiers the shop measures, with their counts — one tap each */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {TIER_FILTERS.map((f) => {
          const count = f.value === "all" ? everyone : tierCounts[f.value] ?? 0;
          return (
            <button
              key={f.value}
              type="button"
              onClick={() => setParam("tier", f.value === "all" ? "" : f.value)}
              aria-pressed={tier === f.value}
              className={`btn btn-sm gap-2 ${tier === f.value ? "btn-secondary text-secondary-content" : "btn-ghost border border-base-300"}`}
            >
              {f.label}
              {data && count > 0 && (
                <span
                  className={`badge badge-sm border-0 ${tier === f.value ? "bg-secondary-content/20 text-secondary-content" : "bg-base-200 text-base-content/70"}`}
                >
                  {count}
                </span>
              )}
            </button>
          );
        })}
        {activeFilters.map((f) => (
          <button
            key={f.key}
            type="button"
            className="btn btn-sm gap-1.5 border border-secondary/40 bg-secondary/10 font-normal text-secondary"
            onClick={() => setParam(f.key, "")}
            aria-label={`Remove the filter ${f.label}: ${f.value}`}
          >
            <span className="opacity-80">{f.label}:</span>
            <span className="font-semibold">{f.value}</span>
            <X size={13} aria-hidden="true" />
          </button>
        ))}
        {filtered && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={clearAll}>
            Reset
          </button>
        )}
      </div>

      {data === null ? (
        <RollSkeleton />
      ) : users.length === 0 ? (
        <div className={`${RAIL} flex flex-col items-center px-6 py-14 text-center`}>
          <span className="flex size-12 items-center justify-center rounded-full bg-secondary/10 text-secondary">
            <UsersRound size={22} aria-hidden="true" />
          </span>
          <p className="font-display mt-4 text-lg font-semibold">{filtered ? "Nobody matches" : "No accounts yet"}</p>
          <p className="mt-1 max-w-sm text-sm opacity-75">
            {filtered ? "Try another search, or clear the filters." : "The first one appears here the moment someone signs up."}
          </p>
          {filtered && (
            <button type="button" className="btn btn-ghost btn-sm mt-4 border border-base-300" onClick={clearAll}>
              Reset
            </button>
          )}
        </div>
      ) : (
        <>
          <p className="mb-2 text-sm opacity-75">
            {total} {total === 1 ? "person" : "people"}
            {searched && <> matching &ldquo;{searched}&rdquo;</>} · {sortPhrase}
          </p>

          {/* On a phone each person is a card and the whole card is the way
              in: nothing wider than the screen, a long name ends in an
              ellipsis instead of pushing. */}
          <ul className={`${RAIL} divide-y divide-base-300/60 md:hidden`}>
            {users.map((u) => (
              <li key={u.id}>
                <button
                  type="button"
                  className="flex w-full items-center gap-3 px-4 py-3.5 text-left transition-colors hover:bg-base-200/50 focus-visible:outline-offset-[-2px] active:bg-base-200/70"
                  onClick={() => setCard(u)}
                  aria-label={`Open ${fullName(u)}`}
                  aria-describedby={`user-card-${u.id}`}
                >
                  <Avatar user={u} />
                  <span id={`user-card-${u.id}`} className="min-w-0 flex-1">
                    <span className="flex items-center justify-between gap-2">
                      <span className="truncate font-semibold">
                        {fullName(u)}
                        {u.id === me.id && <span className="font-normal opacity-65"> (you)</span>}
                      </span>
                      <TierBadge loyalty={u.loyalty} quiet />
                    </span>
                    <span className="block truncate text-xs opacity-70">
                      <Contact user={u} />
                    </span>
                    <span className="mt-1 block text-xs opacity-75">
                      {ordersLabel(u.order_count)}
                      {u.order_count > 0 && ` · ${money(u.lifetime_spend)}`}
                      {u.last_order_at && ` · last ${sinceLabel(u.last_order_at)}`}
                      {!u.is_active && " · deactivated"}
                    </span>
                  </span>
                  <ChevronRight size={18} className="shrink-0 opacity-45" aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>

          {/* From a tablet up, a table: who they are and how to reach them,
              what they ordered and when, where loyalty puts them. A click
              anywhere on a row opens their card; the keyboard has the row's
              own Open button. */}
          <div className={`${RAIL} hidden md:block`}>
            <table className="table">
              <caption className="sr-only">Users, {sortPhrase}</caption>
              <thead>
                <tr className="text-xs tracking-wide uppercase opacity-65">
                  <th scope="col">Person</th>
                  <th scope="col" className="text-right">Orders</th>
                  <th scope="col" className="text-right">Spent</th>
                  <th scope="col" className="hidden lg:table-cell">Last order</th>
                  <th scope="col">Loyalty</th>
                  <th scope="col" className="hidden xl:table-cell">Joined</th>
                  <th scope="col"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id} className="cursor-pointer transition-colors hover:bg-base-200/50" onClick={() => setCard(u)}>
                    {/* The one column that gives: it takes the room the
                        others leave and ends a long name in an ellipsis. */}
                    <td className="w-full max-w-0">
                      <div className="flex items-center gap-3">
                        <Avatar user={u} />
                        <div className="min-w-0">
                          <p className="flex items-center gap-1.5 font-semibold">
                            <span className="truncate">{fullName(u)}</span>
                            {u.id === me.id && <span className="shrink-0 text-xs font-normal opacity-65">(you)</span>}
                            {u.role === "admin" && <span className="badge badge-xs badge-neutral shrink-0">Admin</span>}
                            {!u.is_active && <span className="badge badge-xs badge-outline shrink-0">Deactivated</span>}
                          </p>
                          <p className="flex items-center gap-1 text-xs opacity-70">
                            <span className="truncate">
                              <Contact user={u} />
                            </span>
                            {u.marketing_consent_at && (
                              <MailCheck size={12} className="shrink-0 text-success" aria-label="Can be emailed" />
                            )}
                          </p>
                        </div>
                      </div>
                    </td>
                    <td className="figures text-right font-semibold">{u.order_count}</td>
                    <td className="figures text-right font-semibold whitespace-nowrap">
                      {u.order_count > 0 ? money(u.lifetime_spend) : "—"}
                      {u.order_count > 0 && hasNonActiveCurrency(Object.keys(u.lifetime_spend_by_currency ?? {})) && (
                        <span className="block text-xs font-normal opacity-70">+ other currencies</span>
                      )}
                    </td>
                    <td className="hidden text-sm whitespace-nowrap lg:table-cell">
                      {u.last_order_at ? sinceLabel(u.last_order_at) : <span className="opacity-65">never</span>}
                    </td>
                    <td>
                      <div className="flex flex-wrap items-center gap-1">
                        <TierBadge loyalty={u.loyalty} quiet />
                        {u.classification && (
                          <span className="badge badge-sm badge-outline whitespace-nowrap" title="The desk's word">
                            Word: {TIERS[u.classification].label}
                          </span>
                        )}
                        {(u.loyalty ?? "regular") === "regular" && !u.classification && (
                          <span className="text-xs opacity-65">Regular</span>
                        )}
                      </div>
                    </td>
                    <td className="hidden text-sm whitespace-nowrap opacity-75 xl:table-cell">{formatDate(u.created_at)}</td>
                    <td className="text-right">
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm border border-base-300 whitespace-nowrap"
                        onClick={(e) => {
                          e.stopPropagation();
                          setCard(u);
                        }}
                        aria-label={`Open ${fullName(u)}`}
                      >
                        Open
                        <ChevronRight size={14} aria-hidden="true" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="mt-3 text-center text-xs opacity-70">
            Showing {(data.pagination.page - 1) * data.pagination.per_page + 1}–
            {Math.min(data.pagination.page * data.pagination.per_page, total)} of {total}
          </p>
          <Pager
            label="Pages of users"
            page={data.pagination.page}
            pages={data.pagination.total_pages}
            onPage={(next) => setParam("page", String(next))}
          />
        </>
      )}

      {card !== null && (
        <CustomerDrawer
          user={card}
          voucher={VOUCHER_TIERS.includes(card.loyalty) ? (vouchers?.[card.loyalty] ?? null) : null}
          onClose={() => setCard(null)}
          onChanged={load}
          onUpdate={update}
          onWord={saveWord}
        />
      )}
    </div>
  );
}
