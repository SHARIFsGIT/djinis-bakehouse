import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { Copy, Crown, Download, Heart, Hourglass, ScrollText } from "lucide-react";
import client, { apiError } from "../../api/client";
import { money } from "../../lib/format";
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

export default function AdminUsers() {
  const toast = useToast();
  const { user: me } = useAuth();
  const [params, setParams] = useSearchParams();
  const [data, setData] = useState(null);

  const tier = TIER_FILTERS.some((f) => f.value === params.get("tier"))
    ? params.get("tier")
    : "all";
  const sort = SORTS.some((s) => s.value === params.get("sort"))
    ? params.get("sort")
    : "spend";
  const page = Math.max(1, Number(params.get("page")) || 1);

  // The shelf is served ready-cut by the desk: filtered by tier, ordered,
  // one page at a time — the tier counts ride along so the chips count the
  // whole roll from any page.
  const load = () =>
    client
      .get("/admin/users", {
        params: {
          tier: tier === "all" ? undefined : tier,
          sort,
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
  }, [tier, sort, page]);

  // A new shelf means starting from its first page.
  const setParam = (key, value) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.delete("page");
    setParams(next);
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

  const copyEmail = async (email) => {
    try {
      await navigator.clipboard.writeText(email);
      toast.success(`${email} copied — ready for a voucher code`);
    } catch {
      toast.error("Couldn't reach the clipboard");
    }
  };

  const exportCsv = async () => {
    try {
      const res = await client.get("/admin/users/export", { responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      const link = document.createElement("a");
      link.href = url;
      link.download = "bakehouse-customers.csv";
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(apiError(err, "Export failed"));
    }
  };

  if (data === null) return <Loading label="Rolling out the regulars…" />;

  const tierCounts = data.tier_counts;
  const worthAVoucher = (tierCounts.vip ?? 0) + (tierCounts.loyal ?? 0);
  const users = data.items;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
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
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="select select-bordered select-sm"
            value={sort}
            onChange={(e) => setParam("sort", e.target.value)}
            aria-label="Sort customers"
          >
            {SORTS.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
          <button type="button" className="btn btn-ghost btn-sm gap-2" onClick={exportCsv}>
            <Download size={15} />
            Export CSV
          </button>
        </div>
      </div>

      {worthAVoucher > 0 && (
        <p className="text-sm opacity-70">
          {tierCounts.vip ?? 0} VIP · {tierCounts.loyal ?? 0} loyal
          {tierCounts.lapsed ? ` · ${tierCounts.lapsed} gone quiet` : ""} — the
          regulars a special voucher is for.
        </p>
      )}

      <div className="overflow-x-auto rounded-box glass shadow-sm ring-1 ring-base-300/60">
        <table className="table table-sm">
          <thead>
            <tr className="text-xs uppercase opacity-50">
              <th>Customer</th>
              <th className="text-right">Orders</th>
              <th className="text-right">Lifetime</th>
              <th>Loyalty</th>
              <th>Role</th>
              <th>Active</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
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
                </td>
                <td><TierBadge loyalty={u.loyalty} /></td>
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
                      onClick={() => copyEmail(u.email)}
                      aria-label={`Copy ${u.email}`}
                      title={`Copy ${u.email} — ready for a voucher code`}
                    >
                      <Copy size={13} />
                    </button>
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
            ))}
            {users.length === 0 && (
              <tr>
                <td colSpan={7} className="py-10 text-center text-sm opacity-60">
                  Nobody matches — try another tier.
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
    </div>
  );
}
