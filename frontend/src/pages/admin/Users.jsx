import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { Copy, Crown, Gift, Heart, History, Hourglass, MailCheck, MailX, ScrollText } from "lucide-react";
import client, { apiError } from "../../api/client";
import { formatDateTime, hasNonActiveCurrency, money } from "../../lib/format";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";
import { Loading } from "../../components/Loading";

/* What each tier wears. The rules live on the server (admin.py) — this is
   only their wardrobe. */
const TIERS = {
  vip: {
    label: "VIP",
    Icon: Crown,
    badge: "badge-warning gap-1 text-warning-content",
  },
  loyal: {
    label: "Loyal",
    Icon: Heart,
    badge: "badge-secondary gap-1 text-secondary-content",
  },
  lapsed: {
    label: "Lapsed",
    Icon: Hourglass,
    badge: "badge-ghost gap-1 opacity-70",
  },
  regular: { label: "Regular", Icon: null, badge: "badge-ghost opacity-60" },
};

const TIER_FILTERS = [
  { value: "all", label: "Everyone" },
  { value: "vip", label: "VIP" },
  { value: "loyal", label: "Loyal" },
  { value: "lapsed", label: "Lapsed" },
];

const SORTS = [
  { value: "spend", label: "Top spend" },
  { value: "orders", label: "Most orders" },
  { value: "last", label: "Last order" },
  { value: "joined", label: "Newest members" },
];

/* The desk's manual word for a customer — same vocabulary as the measured
   tiers, but a different thing entirely: this one is the baker's own call,
   written by hand, and nothing ever recalculates it. */
const CLASSIFICATIONS = ["vip", "loyal", "lapsed", "regular"];

const CLASS_FILTERS = [
  { value: "all", label: "Any word" },
  ...CLASSIFICATIONS.map((value) => ({ value, label: TIERS[value].label })),
  { value: "none", label: "No word yet" },
];

/* The tiers that have earned a code of their own — the loyalty the shop
   thanks with a voucher. */
const VOUCHER_TIERS = ["vip", "loyal"];

const PER_PAGE = 10;

function TierBadge({ loyalty }) {
  const tier = TIERS[loyalty] ?? TIERS.regular;
  const Icon = tier.Icon;
  return (
    <span className={`badge badge-sm border ${tier.badge}`}>
      {Icon && <Icon size={11} aria-hidden="true" />}
      {tier.label}
    </span>
  );
}

const wordOf = (value) => (value ? TIERS[value].label : "—");

/* One customer's card: the classification ledger (who set what, from what,
   when) and their marketing permission. Consent is recorded, never assumed —
   a grant must say where it was given and carry evidence the owner could
   show a regulator. */
function CustomerDrawer({ user, onClose, onChanged }) {
  const toast = useToast();
  const [ledger, setLedger] = useState(null);
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

  useEffect(() => {
    client
      .get(`/admin/users/${user.id}/classification-history`)
      .then(({ data }) => setLedger(data))
      .catch((err) => toast.error(apiError(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user.id]);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

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

  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label={`Desk card for ${user.first_name} ${user.last_name}`}>
      <div className="absolute inset-0 bg-black/40" onClick={() => !busy && onClose()} />
      <div className="absolute inset-y-0 right-0 flex w-full max-w-md flex-col bg-base-100 shadow-2xl">
        <div className="flex items-center justify-between border-b border-base-300/70 px-6 py-4">
          <div>
            <h2 className="font-display text-xl font-semibold">
              {user.first_name} {user.last_name}
            </h2>
            <p className="text-xs opacity-60">{user.email}</p>
          </div>
          <button type="button" className="btn btn-ghost btn-sm btn-square" onClick={onClose} aria-label="Close" disabled={busy}>
            ✕
          </button>
        </div>

        <div className="flex-1 space-y-6 overflow-y-auto px-6 py-5">
          {/* Marketing permission — the gate every campaign and voucher email passes */}
          <section>
            <h3 className="font-display text-sm font-bold uppercase tracking-widest opacity-55">
              Marketing permission
            </h3>
            {consent.marketing_consent_at ? (
              <div className="mt-2 rounded-box border border-success/30 bg-success/10 p-4 text-sm">
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
            ) : (
              <form
                className="mt-2 space-y-3 rounded-box border border-base-300 bg-base-200/50 p-4"
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
          </section>

          {/* The classification ledger — newest first, exactly as the server keeps it */}
          <section>
            <h3 className="font-display text-sm font-bold uppercase tracking-widest opacity-55">
              Classification history
            </h3>
            {ledger === null ? (
              <div className="mt-2 space-y-2" aria-label="Loading the ledger">
                <div className="skeleton h-4 w-full" />
                <div className="skeleton h-4 w-2/3" />
              </div>
            ) : ledger.length === 0 ? (
              <p className="mt-2 text-xs opacity-60">
                No changes recorded yet — the desk hasn't set a word for this customer.
              </p>
            ) : (
              <ul className="mt-2 space-y-2">
                {ledger.map((event) => (
                  <li key={event.id} className="rounded-box border border-base-300/70 bg-base-200/40 px-3 py-2 text-xs">
                    <span className="font-semibold">{wordOf(event.from_classification)}</span>
                    {" → "}
                    <span className="font-semibold text-secondary">{wordOf(event.to_classification)}</span>
                    <span className="opacity-60">
                      {" "}· {event.admin_name} · {formatDateTime(event.created_at)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

export default function AdminUsers() {
  const toast = useToast();
  const { user: me } = useAuth();
  const [params, setParams] = useSearchParams();
  const [data, setData] = useState(null);
  const [search, setSearch] = useState(params.get("search") ?? "");
  const [vouchers, setVouchers] = useState(null); // { vip: coupon|null, loyal: coupon|null }
  const [coupons, setCoupons] = useState([]);
  const [card, setCard] = useState(null); // the customer whose desk card is open

  const tier = TIER_FILTERS.some((f) => f.value === params.get("tier"))
    ? params.get("tier")
    : "all";
  const cls = CLASS_FILTERS.some((f) => f.value === params.get("classification"))
    ? params.get("classification")
    : "all";
  const sort = SORTS.some((s) => s.value === params.get("sort"))
    ? params.get("sort")
    : "spend";
  const searched = params.get("search") ?? "";
  const page = Math.max(1, Number(params.get("page")) || 1);

  // The shelf is served ready-cut by the desk: filtered by tier and by the
  // desk's manual word, searched, ordered, one page at a time — the tier and
  // classification counts ride along so the chips count the whole roll from
  // any page.
  const load = () =>
    client
      .get("/admin/users", {
        params: {
          tier: tier === "all" ? undefined : tier,
          classification: cls === "all" ? undefined : cls,
          sort,
          search: searched || undefined,
          page,
          per_page: PER_PAGE,
        },
      })
      .then(({ data }) => setData(data))
      .catch((err) => toast.error(apiError(err)));

  useEffect(() => {
    setData(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tier, cls, sort, searched, page]);

  // Which coupon each tier hands out, plus the tin to choose from — once
  // per visit, not per shelf.
  useEffect(() => {
    Promise.all([
      client.get("/admin/loyalty-vouchers"),
      client.get("/admin/coupons"),
    ])
      .then(([v, c]) => {
        setVouchers(v.data);
        setCoupons(c.data);
      })
      .catch((err) => toast.error(apiError(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A new shelf means starting from its first page.
  const setParam = (key, value) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.delete("page");
    setParams(next);
  };

  const submitSearch = (e) => {
    e.preventDefault();
    setParam("search", search.trim());
    setSearch(search.trim());
  };

  const update = async (id, payload) => {
    try {
      await client.put(`/admin/users/${id}`, payload);
      await load();
      toast.success("User updated");
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  // The desk's own word for a customer — a different thing from the measured
  // tier beside it: hand-set, hand-cleared, and every change lands in the
  // ledger the drawer shows.
  const setWord = async (user, value) => {
    try {
      await client.put(`/admin/users/${user.id}/classification`, {
        classification: value || null,
      });
      await load();
      toast.success(
        value
          ? `${user.first_name} marked ${TIERS[value].label} at the desk`
          : `Desk's word cleared for ${user.first_name}`,
      );
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  const copyCode = async (coupon, forName) => {
    if (!coupon) return;
    try {
      await navigator.clipboard.writeText(coupon.code);
      toast.success(
        `${coupon.code} copied — ${coupon.percent_off}% off${forName ? ` for ${forName}` : ""}`,
      );
    } catch {
      toast.error("Couldn't reach the clipboard");
    }
  };

  const linkVoucher = async (tierKey, couponId) => {
    try {
      const { data: next } = await client.put(
        `/admin/loyalty-vouchers/${tierKey}`,
        { coupon_id: couponId },
      );
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

  if (data === null) return <Loading label="Rolling out the regulars…" />;

  const tierCounts = data.tier_counts;
  const worthAVoucher = (tierCounts.vip ?? 0) + (tierCounts.loyal ?? 0);
  const users = data.items;

  return (
    <div className="space-y-4">
      {/* One shelf-head: tier chips, the search field, and the sort — a
          single row that wraps with grace on a phone */}
      <div className="flex flex-wrap items-center gap-2">
        {TIER_FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            onClick={() => setParam("tier", f.value === "all" ? "" : f.value)}
            className={`btn btn-sm gap-2 ${tier === f.value ? "btn-secondary text-secondary-content" : "btn-ghost border border-base-300"}`}
          >
            {f.label}
            {f.value !== "all" && tierCounts[f.value] > 0 && (
              <span
                className={`badge badge-sm border-0 ${tier === f.value ? "bg-secondary-content/20 text-secondary-content" : "bg-base-200 text-base-content/70"}`}
              >
                {tierCounts[f.value]}
              </span>
            )}
          </button>
        ))}
        <form onSubmit={submitSearch} className="join">
          <input
            className="input input-bordered input-sm join-item w-40 sm:w-52"
            placeholder="Search name, email, phone…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search customers"
          />
          <button type="submit" className="btn btn-primary btn-sm join-item">
            Search
          </button>
        </form>
        {searched && (
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => {
              setSearch("");
              setParam("search", "");
            }}
          >
            Clear
          </button>
        )}
        <select
          className="select select-bordered select-sm sm:ml-auto"
          value={sort}
          onChange={(e) => setParam("sort", e.target.value)}
          aria-label="Sort customers"
        >
          {SORTS.map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
      </div>

      {/* The desk's manual word — a second, quieter row: these narrow by the
          hand-set classification, not the order book's arithmetic. The
          unclassified count is the whole roll minus the classified ones. */}
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="mr-1 font-semibold uppercase tracking-widest opacity-50">
          Desk's word
        </span>
        {CLASS_FILTERS.map((f) => {
          const count =
            f.value === "all"
              ? null
              : f.value === "none"
                ? Object.values(tierCounts).reduce((a, b) => a + b, 0) -
                  Object.values(data.classification_counts ?? {}).reduce((a, b) => a + b, 0)
                : (data.classification_counts ?? {})[f.value] ?? 0;
          return (
            <button
              key={f.value}
              type="button"
              onClick={() => setParam("classification", f.value === "all" ? "" : f.value)}
              className={`btn btn-xs gap-1.5 rounded-full ${cls === f.value ? "btn-secondary text-secondary-content" : "btn-ghost border border-base-300"}`}
            >
              {f.label}
              {count !== null && count > 0 && (
                <span className={`badge badge-xs border-0 ${cls === f.value ? "bg-secondary-content/20 text-secondary-content" : "bg-base-200 text-base-content/60"}`}>
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* The codes loyalty earns — one per tier, chosen from the coupon tin */}
      {worthAVoucher > 0 && (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-box glass px-4 py-3 text-sm shadow-sm ring-1 ring-base-300/60">
          {VOUCHER_TIERS.map((key) => {
            const t = TIERS[key];
            const coupon = vouchers?.[key];
            const Icon = t.Icon;
            return (
              <div key={key} className="flex flex-wrap items-center gap-2">
                <span className="flex items-center gap-1.5 font-semibold">
                  <Icon size={14} className="text-secondary" aria-hidden="true" />
                  {t.label}
                  <span className="opacity-60">
                    ({tierCounts[key] ?? 0})
                  </span>
                </span>
                {coupon ? (
                  <>
                    <span className="badge badge-sm border font-mono font-bold">
                      {coupon.code} · {coupon.percent_off}% off
                    </span>
                    <button
                      type="button"
                      className="btn btn-ghost btn-xs btn-square"
                      onClick={() => copyCode(coupon)}
                      aria-label={`Copy the ${t.label} code`}
                      title={`Copy ${coupon.code}`}
                    >
                      <Copy size={13} />
                    </button>
                  </>
                ) : (
                  <span className="opacity-50">no code yet</span>
                )}
                <select
                  className="select select-bordered select-xs max-w-40"
                  value={coupon?.id ?? ""}
                  onChange={(e) => linkVoucher(key, e.target.value ? Number(e.target.value) : null)}
                  aria-label={`Coupon for the ${t.label} tier`}
                >
                  <option value="">— pick a coupon —</option>
                  {coupons.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.code} · {c.percent_off}%{c.is_active ? "" : " (paused)"}
                    </option>
                  ))}
                </select>
              </div>
            );
          })}
        </div>
      )}

      <div className="overflow-x-auto rounded-box glass shadow-sm ring-1 ring-base-300/60">
        <table className="table table-sm">
          <thead>
            <tr className="text-xs uppercase opacity-50">
              <th>Customer</th>
              <th className="text-right">Orders</th>
              <th className="text-right">Lifetime</th>
              <th>Loyalty</th>
              <th>Desk's word</th>
              <th>Role</th>
              <th>Active</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              const voucher = VOUCHER_TIERS.includes(u.loyalty) ? vouchers?.[u.loyalty] : null;
              return (
                <tr key={u.id} className="hover:bg-base-200/50">
                  <td>
                    <div className="flex items-center gap-3">
                      {u.avatar_url ? (
                        <img
                          src={u.avatar_url}
                          alt=""
                          className="h-9 w-9 shrink-0 rounded-full object-cover ring-1 ring-base-300"
                        />
                      ) : (
                        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-secondary/15 text-sm font-bold text-secondary">
                          {u.first_name?.[0]?.toUpperCase() ?? "?"}
                        </span>
                      )}
                      <div>
                        <p className="font-semibold">
                          {u.first_name} {u.last_name}
                          {u.id === me.id && <span className="ml-1 text-xs opacity-50">(you)</span>}
                        </p>
                        <p className="text-xs opacity-60">{u.email}{u.phone ? ` · ${u.phone}` : ""}</p>
                      </div>
                    </div>
                  </td>
                  <td className="text-right font-bold">{u.order_count}</td>
                  <td className="text-right font-bold">
                    {u.order_count > 0 ? money(u.lifetime_spend) : "—"}
                    {u.order_count > 0 &&
                      hasNonActiveCurrency(Object.keys(u.lifetime_spend_by_currency ?? {})) && (
                        <span className="block text-[11px] font-normal opacity-60">
                          {Object.entries(u.lifetime_spend_by_currency)
                            .map(([code, amount]) => money(amount, code))
                            .join(" · ")}
                        </span>
                      )}
                  </td>
                  <td><TierBadge loyalty={u.loyalty} /></td>
                  <td>
                    <div className="flex items-center gap-1">
                      <select
                        className="select select-bordered select-xs"
                        value={u.classification ?? ""}
                        onChange={(e) => setWord(u, e.target.value)}
                        aria-label={`Desk's word for ${u.email}`}
                      >
                        <option value="">— none —</option>
                        {CLASSIFICATIONS.map((c) => (
                          <option key={c} value={c}>{TIERS[c].label}</option>
                        ))}
                      </select>
                      {/* Consent at a glance: a tick means recorded consent,
                          the muted slash means nothing marketing may be sent */}
                      {u.marketing_consent_at ? (
                        <span className="text-success" title="Recorded marketing consent">
                          <MailCheck size={13} aria-label="Has marketing consent" />
                        </span>
                      ) : (
                        <span className="opacity-30" title="No recorded consent">
                          <MailX size={13} aria-label="No marketing consent" />
                        </span>
                      )}
                    </div>
                  </td>
                  <td>
                    <select
                      className="select select-bordered select-xs"
                      value={u.role}
                      onChange={(e) => update(u.id, { role: e.target.value })}
                      aria-label={`Role for ${u.email}`}
                    >
                      <option value="customer">customer</option>
                      <option value="admin">admin</option>
                    </select>
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      className="toggle toggle-success toggle-sm"
                      checked={u.is_active}
                      disabled={u.id === me.id}
                      onChange={(e) => update(u.id, { is_active: e.target.checked })}
                      aria-label={`Active toggle for ${u.email}`}
                    />
                  </td>
                  <td>
                    <div className="flex items-center justify-end gap-1">
                      <button
                        type="button"
                        className="btn btn-ghost btn-xs btn-square"
                        onClick={() => setCard(u)}
                        aria-label={`Desk card for ${u.first_name}: classification history and marketing consent`}
                        title="Desk card — history & marketing permission"
                      >
                        <History size={13} />
                      </button>
                      {voucher && (
                        <button
                          type="button"
                          className="btn btn-ghost btn-xs btn-square text-secondary"
                          onClick={() => copyCode(voucher, u.first_name)}
                          aria-label={`Copy the ${TIERS[u.loyalty].label} code for ${u.first_name}`}
                          title={`Copy ${voucher.code} — the code their loyalty earns`}
                        >
                          <Gift size={13} />
                        </button>
                      )}
                      {u.order_count > 0 && (
                        <Link
                          to={`/admin/orders?search=${encodeURIComponent(u.email)}`}
                          className="btn btn-ghost btn-xs btn-square"
                          aria-label={`Orders for ${u.first_name}`}
                          title="See their orders"
                        >
                          <ScrollText size={13} />
                        </Link>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
            {users.length === 0 && (
              <tr>
                <td colSpan={8} className="py-10 text-center text-sm opacity-60">
                  Nobody matches — try another tier or search.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {data.pagination.total_pages > 1 && (
        <div className="flex justify-center">
          <div className="join">
            <button
              className="btn join-item btn-sm"
              disabled={page <= 1}
              onClick={() => setParam("page", String(page - 1))}
            >
              «
            </button>
            <button className="btn join-item btn-sm pointer-events-none">
              Page {data.pagination.page} / {data.pagination.total_pages}
            </button>
            <button
              className="btn join-item btn-sm"
              disabled={page >= data.pagination.total_pages}
              onClick={() => setParam("page", String(page + 1))}
            >
              »
            </button>
          </div>
        </div>
      )}

      {card !== null && (
        <CustomerDrawer
          user={card}
          onClose={() => setCard(null)}
          onChanged={load}
        />
      )}
    </div>
  );
}
