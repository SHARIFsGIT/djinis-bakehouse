import { useEffect, useState } from "react";
import { Link } from "react-router";
import { BadgeEuro, ScrollText, TrendingUp, Users } from "lucide-react";
import client from "../../api/client";
import { formatDate, hasNonActiveCurrency, money, ORDER_STATUSES, statusBadge, statusLabel } from "../../lib/format";
import PhotoTile from "../../components/PhotoTile";
import { Loading } from "../../components/Loading";

function StatTile({ label, value, sub, Icon }) {
  return (
    <div className="glass rounded-box p-5 shadow-sm ring-1 ring-base-300/60">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-bold tracking-widest uppercase opacity-50">{label}</p>
        {Icon && (
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-secondary/15 text-secondary">
            <Icon size={16} />
          </span>
        )}
      </div>
      <p className="font-display mt-2 text-3xl font-bold">{value}</p>
      {sub && <p className="mt-1 text-xs opacity-60">{sub}</p>}
    </div>
  );
}

export default function Dashboard() {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    client.get("/admin/stats").then(({ data }) => setStats(data));
  }, []);

  if (!stats) return <Loading label="Counting the day's loaves…" />;

  const activeStatuses = ORDER_STATUSES.filter((s) => (stats.orders_by_status[s.value] ?? 0) > 0);
  const maxStatus = Math.max(...activeStatuses.map((s) => stats.orders_by_status[s.value]), 1);
  const maxTop = Math.max(...stats.top_products.map((t) => t.quantity), 1);
  // Money never mixes: the headline tiles speak the shop's active currency,
  // and the split rides alongside whenever any money sits in another one —
  // even a single bucket of history after a cutover must stay visible.
  const currencies = stats.revenue_by_currency ?? [];
  const mixed = hasNonActiveCurrency(currencies.map((c) => c.currency));

  return (
    <div className="space-y-8">
      {/* Headline stats */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile Icon={BadgeEuro} label="Paid revenue" value={money(stats.paid_revenue)} sub={`${money(stats.total_revenue)} incl. pending${mixed ? " · active currency" : ""}`} />
        <StatTile
          Icon={ScrollText}
          label="Orders"
          value={stats.order_count}
          sub={
            stats.new_enquiries > 0
              ? `${stats.pending_orders} to confirm · ${stats.new_enquiries} new enquir${stats.new_enquiries === 1 ? "y" : "ies"}`
              : `${stats.pending_orders} awaiting confirmation`
          }
        />
        <StatTile Icon={Users} label="Customers" value={stats.customer_count} sub="registered regulars" />
        <StatTile Icon={TrendingUp} label="Avg order" value={money(stats.avg_order_value)} sub={mixed ? "active currency only" : "across all orders"} />
      </div>

      {mixed && (
        <div className="glass rounded-box p-5 shadow-sm ring-1 ring-base-300/60">
          <h2 className="font-display text-lg font-semibold">Revenue by currency</h2>
          <p className="mt-1 text-xs opacity-60">
            Currencies are never combined — each column is its own money.
          </p>
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {currencies.map((c) => (
              <div key={c.currency} className="rounded-xl bg-base-200/50 px-4 py-3">
                <p className="text-xs font-bold tracking-widest uppercase opacity-50">{c.currency}</p>
                <p className="font-display mt-1 text-xl font-bold">{money(c.paid_revenue, c.currency)} <span className="text-xs font-normal opacity-60">paid</span></p>
                <p className="text-xs opacity-60">
                  {money(c.total_revenue, c.currency)} total · {money(c.avg_order_value, c.currency)} avg · {c.order_count} order{c.order_count === 1 ? "" : "s"}
                  {c.refunded_revenue > 0 && ` · ${money(c.refunded_revenue, c.currency)} refunded`}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        {/* Status breakdown */}
        <section className="glass rounded-box p-6 shadow-sm ring-1 ring-base-300/60">
          <h2 className="font-display text-xl font-semibold">Orders by status</h2>
          {activeStatuses.length === 0 ? (
            <p className="mt-4 text-sm opacity-60">No orders yet today.</p>
          ) : (
            <ul className="mt-4 space-y-3">
              {activeStatuses.map((s) => {
                const count = stats.orders_by_status[s.value];
                return (
                  <li key={s.value} className="flex items-center gap-3 text-sm">
                    <span className="w-32 shrink-0">{s.label}</span>
                    <div className="h-3 flex-1 overflow-hidden rounded-full bg-base-200">
                      <div
                        className="brand-gradient h-full rounded-full transition-all"
                        style={{ width: `${(count / maxStatus) * 100}%` }}
                      />
                    </div>
                    <span className="w-8 text-right font-bold">{count}</span>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {/* Top products */}
        <section className="glass rounded-box p-6 shadow-sm ring-1 ring-base-300/60">
          <h2 className="font-display text-xl font-semibold">Best sellers</h2>
          {stats.top_products.length === 0 ? (
            <p className="mt-4 text-sm opacity-60">No sales yet — get baking!</p>
          ) : (
            <ul className="mt-4 space-y-3">
              {stats.top_products.map((t, i) => (
                <li key={t.name} className="flex items-center gap-3 text-sm">
                  <span className="font-display w-6 text-center font-bold opacity-40">{i + 1}</span>
                  <span className="min-w-0 flex-1 truncate font-semibold">{t.name}</span>
                  <div className="h-3 w-28 overflow-hidden rounded-full bg-base-200">
                    <div
                      className="brand-gradient h-full rounded-full transition-all"
                      style={{ width: `${(t.quantity / maxTop) * 100}%` }}
                    />
                  </div>
                  <span className="w-14 text-right font-bold">{t.quantity} sold</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1fr_360px]">
        {/* Recent orders */}
        <section className="glass rounded-box p-6 shadow-sm ring-1 ring-base-300/60">
          <div className="flex items-center justify-between">
            <h2 className="font-display text-xl font-semibold">Latest orders</h2>
            <Link to="/admin/orders" className="link link-secondary text-xs font-semibold">Manage all →</Link>
          </div>
          {stats.recent_orders.length === 0 ? (
            <p className="mt-4 text-sm opacity-60">Nothing on the rack yet.</p>
          ) : (
            <div className="mt-4 overflow-x-auto">
              <table className="table table-sm">
                <thead>
                  <tr className="text-xs uppercase opacity-50">
                    <th>Order</th>
                    <th>Recipient</th>
                    <th>Status</th>
                    <th className="text-right">Total</th>
                    <th className="text-right">Placed</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.recent_orders.map((o) => (
                    <tr key={o.id} className="hover:bg-base-200/50">
                      <td>
                        <Link to={`/admin/orders?search=${o.order_number}`} className="font-bold hover:text-secondary">
                          {o.order_number}
                        </Link>
                      </td>
                      <td className="max-w-32 truncate">{o.recipient}</td>
                      <td>
                        <span className={`badge badge-sm ${statusBadge(o.status)} border`}>{statusLabel(o.status)}</span>
                      </td>
                      <td className="text-right font-bold">{money(o.total, o.currency)}</td>
                      <td className="text-right text-xs opacity-60">{formatDate(o.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Low stock */}
        <section className="glass rounded-box p-6 shadow-sm ring-1 ring-base-300/60">
          <div className="flex items-center justify-between">
            <h2 className="font-display text-xl font-semibold">Running low</h2>
            <span className="badge badge-warning badge-sm">stock ≤ 5</span>
          </div>
          {stats.low_stock.length === 0 ? (
            <p className="mt-4 text-sm opacity-60">Shelves are full.</p>
          ) : (
            <ul className="mt-4 space-y-2">
              {stats.low_stock.map((p) => (
                <li key={p.id} className="flex items-center gap-3 rounded-xl bg-base-200/50 px-3 py-2 text-sm">
                  <span className="h-9 w-9 shrink-0 overflow-hidden rounded-lg">
                    <PhotoTile src={p.image_url} name={p.name} className="h-full w-full" />
                  </span>
                  <span className="min-w-0 flex-1 truncate font-semibold">{p.name}</span>
                  <span className={`badge badge-sm ${p.stock === 0 ? "badge-error" : "badge-warning"}`}>
                    {p.stock === 0 ? "out" : `${p.stock} left`}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <Link to="/admin/products" className="btn btn-primary btn-sm btn mt-4 w-full">
            Restock the case
          </Link>
        </section>
      </div>
    </div>
  );
}
