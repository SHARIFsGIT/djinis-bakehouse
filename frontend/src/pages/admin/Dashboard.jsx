import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import {
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  ChevronRight,
  CircleCheck,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";
import client, { apiError } from "../../api/client";
import { formatDate, hasNonActiveCurrency, money, statusBadge, statusLabel } from "../../lib/format";
import TradingChart from "../../components/TradingChart";
import { LoadError } from "../../components/Loading";

/* The desk's first page, in the order a shift asks its questions:
   what is waiting for me, what is due when, how is the month going — and
   only then the reference lists. */

const PANEL = "glass rounded-box p-5 shadow-sm ring-1 ring-base-300/60 sm:p-6";
const ROW = "-mx-2 rounded-lg px-2 py-3 transition-colors hover:bg-base-200/70";

const count = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
// A calendar date ("2026-11-23") at midday, so no timezone can tip it over.
const asDay = (iso) => new Date(`${iso}T12:00:00`);
const daysApart = (fromIso, toIso) => Math.round((asDay(toIso) - asDay(fromIso)) / 86_400_000);

/** The page's shape while the figures are on their way. */
function DeskSkeleton() {
  return (
    <div className="space-y-6" role="status">
      <span className="sr-only">Loading the dashboard…</span>
      <div className="skeleton h-8 w-64 max-w-full" />
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[2fr_3fr]">
        <div className="skeleton h-48 rounded-box" />
        <div className="skeleton h-48 rounded-box" />
      </div>
      <div className="skeleton h-96 rounded-box" />
    </div>
  );
}

/** What only the baker can move along: each row is the count, what it is,
    and the desk that deals with it. */
function WaitingForYou({ stats }) {
  const low = stats.low_stock ?? [];
  const lowCount = Math.max(stats.low_stock_count ?? 0, low.length);
  const named = low.slice(0, 3).map((p) => `${p.name} (${p.stock === 0 ? "sold out" : `${p.stock} left`})`);

  const tasks = [
    stats.pending_orders > 0 && {
      key: "confirm",
      n: stats.pending_orders,
      title: stats.pending_orders === 1 ? "Order to confirm" : "Orders to confirm",
      detail: "Placed, and waiting for you to accept.",
      to: "/admin/orders?status=pending",
    },
    stats.new_enquiries > 0 && {
      key: "enquiries",
      n: stats.new_enquiries,
      title: stats.new_enquiries === 1 ? "Catering enquiry to answer" : "Catering enquiries to answer",
      detail: "New, with no reply yet.",
      to: "/admin/orders?tab=bulk&bulk_status=new",
    },
    lowCount > 0 && {
      key: "stock",
      n: lowCount,
      title: lowCount === 1 ? "Bake running low" : "Bakes running low",
      detail: `${named.join(", ")}${lowCount > named.length ? ` and ${lowCount - named.length} more` : ""}`,
      to: "/admin/products",
    },
  ].filter(Boolean);

  return (
    <section className={PANEL} aria-labelledby="desk-waiting">
      <h2 id="desk-waiting" className="font-display text-xl font-semibold">Waiting for you</h2>
      {tasks.length === 0 ? (
        <p className="mt-3 flex items-start gap-2.5 text-sm">
          <CircleCheck size={18} className="mt-0.5 shrink-0 text-success" aria-hidden="true" />
          <span>
            Nothing right now — no orders to confirm, no enquiries to answer, and the shelves are stocked.
          </span>
        </p>
      ) : (
        <ul className="mt-2 divide-y divide-base-300/70">
          {tasks.map((task) => (
            <li key={task.key}>
              <Link to={task.to} className={`${ROW} flex items-center gap-4`}>
                <span className="min-w-9 shrink-0 text-center text-2xl leading-none font-semibold">{task.n}</span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold">{task.title}</span>
                  <span className="block text-xs opacity-70">{task.detail}</span>
                </span>
                <ChevronRight size={16} className="shrink-0 opacity-60" aria-hidden="true" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Open orders that carry a date, soonest first. A date that has passed
    with the order still open leads the list, and says so in words. */
function DueNext({ stats }) {
  const due = stats.due_orders ?? [];
  const today = stats.shop_date;
  const hidden = Math.max(0, (stats.due_count ?? 0) - due.length);

  const when = (iso) => {
    const days = today ? daysApart(today, iso) : 1;
    if (days === 0) return { label: "Today" };
    if (days === 1 && today) return { label: "Tomorrow" };
    const options = { weekday: "short", month: "short", day: "numeric" };
    if (days < 0) return { label: asDay(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" }), late: true };
    return { label: asDay(iso).toLocaleDateString("en-US", options) };
  };

  return (
    <section className={PANEL} aria-labelledby="desk-due">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id="desk-due" className="font-display text-xl font-semibold">Due next</h2>
        {(stats.overdue_orders > 0 || stats.due_today > 0) && (
          <p className="text-xs">
            {stats.overdue_orders > 0 && (
              <span className="text-caution font-semibold">{stats.overdue_orders} overdue</span>
            )}
            {stats.overdue_orders > 0 && stats.due_today > 0 && <span className="opacity-70"> · </span>}
            {stats.due_today > 0 && <span className="opacity-70">{stats.due_today} today</span>}
          </p>
        )}
      </div>
      {due.length === 0 ? (
        <p className="mt-3 text-sm opacity-70">
          No dated orders on the way. When a customer picks a delivery date at checkout, the order lines up
          here, soonest first.
        </p>
      ) : (
        <ul className="mt-2 divide-y divide-base-300/70">
          {due.map((order) => {
            const day = when(order.delivery_date);
            return (
              <li key={order.id}>
                {/* A phone gets two lines — the day and where the order
                    stands, then whose it is; from a tablet up it is one. */}
                <Link
                  to={`/admin/orders?search=${encodeURIComponent(order.order_number)}`}
                  className={`${ROW} grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 sm:grid-cols-[8.5rem_1fr_auto]`}
                >
                  <span className="text-sm font-semibold">
                    {day.late ? (
                      <span className="text-caution inline-flex items-center gap-1">
                        <TriangleAlert size={14} className="shrink-0" aria-hidden="true" />
                        Was due {day.label}
                      </span>
                    ) : (
                      day.label
                    )}
                  </span>
                  <span className="order-last col-span-2 min-w-0 sm:order-none sm:col-span-1">
                    <span className="block truncate text-sm">
                      <span className="font-semibold">{order.order_number}</span> · {order.recipient}
                    </span>
                    <span className="figures block text-xs opacity-70">
                      {count(order.item_count, "item")} · {money(order.total, order.currency)}
                    </span>
                  </span>
                  <span className={`badge badge-sm ${statusBadge(order.status)} border whitespace-nowrap`}>
                    {statusLabel(order.status)}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      {hidden > 0 && (
        <p className="mt-3 text-xs opacity-70">
          And {count(hidden, "more dated order")} after these — all of them are in{" "}
          <Link to="/admin/orders" className="link link-secondary font-semibold">
            Orders
          </Link>
          .
        </p>
      )}
    </section>
  );
}

/** How a figure moved against the stretch before: an arrow and a signed
    amount, in words a screen reader can say. Money moves in percent; a
    handful of orders or sign-ups moves in whole numbers ("+3" says more
    than "+150%"). */
function Change({ now, before, whole = false }) {
  if (!before) return <span className="opacity-70">{now ? "none before" : "—"}</span>;
  const diff = now - before;
  const percent = Math.round((Math.abs(diff) / before) * 100);
  if (whole ? diff === 0 : percent === 0) return <span className="opacity-70">no change</span>;

  const up = diff > 0;
  const Icon = up ? ArrowUpRight : ArrowDownRight;
  const sign = up ? "+" : "−";
  let amount = whole ? `${sign}${Math.abs(diff)}` : `${sign}${percent}%`;
  // Ten times last month is "10×", not "+900%".
  if (!whole && now / before >= 10) amount = `${Math.round(now / before)}×`;

  return (
    <span className={`inline-flex items-center gap-0.5 font-semibold ${up ? "text-success" : "text-error"}`}>
      <Icon size={14} className="shrink-0" aria-hidden="true" />
      <span className="sr-only">{up ? "Up" : "Down"}: </span>
      {amount}
    </span>
  );
}

function Figure({ label, value, now, before, was, whole = false, compare = true }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs opacity-70">{label}</dt>
      <dd className="mt-1 text-xl font-semibold tracking-tight [overflow-wrap:anywhere] sm:text-2xl">{value}</dd>
      <dd className="mt-1 flex min-h-4 flex-wrap items-center gap-x-1.5 text-xs">
        {compare && <Change now={now} before={before} whole={whole} />}
        {compare && before > 0 && <span className="opacity-70">from {was}</span>}
      </dd>
    </div>
  );
}

/** The month as four figures and one chart — one measure throughout: kept
    orders in the shop's active currency, by the day they were placed. */
function ThirtyDays({ stats, mixed }) {
  const now = stats.period;
  const before = stats.previous_period;

  return (
    <section className={PANEL} aria-labelledby="desk-month">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id="desk-month" className="font-display text-xl font-semibold">The last 30 days</h2>
        <p className="text-xs opacity-70">
          Compared with the 30 days before{mixed ? " · active currency only" : ""}
        </p>
      </div>

      {now && before && (
        <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-6 sm:grid-cols-4">
          <Figure
            label="Order value"
            value={money(now.revenue)}
            now={now.revenue}
            before={before.revenue}
            was={money(before.revenue)}
          />
          <Figure label="Orders" value={now.orders} now={now.orders} before={before.orders} was={before.orders} whole />
          <Figure
            label="Average order"
            value={now.orders > 0 ? money(now.avg_order_value) : "—"}
            now={now.avg_order_value}
            before={before.avg_order_value}
            was={money(before.avg_order_value)}
            compare={now.orders > 0}
          />
          <Figure
            label="New customers"
            value={now.new_customers}
            now={now.new_customers}
            before={before.new_customers}
            was={before.new_customers}
            whole
          />
        </dl>
      )}

      <TradingChart series={stats.revenue_series} />

      <p className="mt-5 border-t border-base-300/70 pt-4 text-xs opacity-70">
        All time: {money(stats.paid_revenue)} paid
        {stats.total_revenue > stats.paid_revenue && ` of ${money(stats.total_revenue)} ordered`}
        {" · "}
        {count(stats.order_count, "order")} · {count(stats.customer_count, "customer")}
      </p>
    </section>
  );
}

function LatestOrders({ orders }) {
  return (
    <section className={PANEL} aria-labelledby="desk-latest">
      <div className="flex items-baseline justify-between gap-4">
        <h2 id="desk-latest" className="font-display text-xl font-semibold">Latest orders</h2>
        <Link to="/admin/orders" className="link link-secondary inline-flex items-center gap-1 text-xs font-semibold">
          All orders
          <ArrowRight size={13} aria-hidden="true" />
        </Link>
      </div>
      {orders.length === 0 ? (
        <p className="mt-3 text-sm opacity-70">
          No orders yet. The first one lands here the moment a customer checks out.
        </p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="table table-sm">
            <thead>
              {/* Two columns on a phone, like the order rail: the order
                  with who it is for, and where it stands with what it
                  comes to. (Five columns there broke the order number
                  over three lines and the status pill over two.) */}
              <tr className="text-xs opacity-70">
                <th scope="col">Order</th>
                <th scope="col" className="hidden sm:table-cell">For</th>
                <th scope="col">Status</th>
                <th scope="col" className="hidden text-right sm:table-cell">Total</th>
                <th scope="col" className="hidden text-right sm:table-cell">Placed</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.id} className="hover:bg-base-200/50">
                  <td className="whitespace-nowrap">
                    <Link
                      to={`/admin/orders?search=${encodeURIComponent(o.order_number)}`}
                      className="font-bold hover:text-secondary"
                    >
                      {o.order_number}
                    </Link>
                    <span className="block max-w-40 truncate text-xs opacity-70 sm:hidden">{o.recipient}</span>
                  </td>
                  <td className="hidden max-w-32 truncate sm:table-cell">{o.recipient}</td>
                  <td>
                    <span className={`badge badge-sm ${statusBadge(o.status)} border whitespace-nowrap`}>
                      {statusLabel(o.status)}
                    </span>
                    <span className="figures mt-1 block text-xs font-bold sm:hidden">{money(o.total, o.currency)}</span>
                  </td>
                  <td className="figures hidden text-right font-bold whitespace-nowrap sm:table-cell">
                    {money(o.total, o.currency)}
                  </td>
                  <td className="hidden text-right text-xs whitespace-nowrap opacity-70 sm:table-cell">
                    {formatDate(o.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** A ranked list with its bars: one series, so one colour — the length
    says "how many", the figure beside the name says exactly. */
function BestSellers({ products }) {
  const most = Math.max(...products.map((t) => t.quantity), 1);
  return (
    <section className={PANEL} aria-labelledby="desk-sellers">
      <h2 id="desk-sellers" className="font-display text-xl font-semibold">Best sellers</h2>
      <p className="mt-0.5 text-xs opacity-70">All time, by pieces sold</p>
      {products.length === 0 ? (
        <p className="mt-3 text-sm opacity-70">
          Nothing sold yet. Your five most-ordered bakes rank here once orders come in.
        </p>
      ) : (
        <ol className="mt-4 space-y-3.5">
          {products.map((t) => (
            <li key={t.name}>
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span className="min-w-0 truncate font-semibold">{t.name}</span>
                <span className="figures shrink-0 text-xs opacity-70">{t.quantity} sold</span>
              </div>
              {/* Square where it starts, rounded where it ends */}
              <div
                className="mt-1.5 h-2 rounded-r-[4px] bg-secondary"
                style={{ width: `${Math.max(2, (t.quantity / most) * 100)}%` }}
              />
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [failed, setFailed] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [loadedAt, setLoadedAt] = useState(null);
  const inFlight = useRef(false);

  const load = useCallback(() => {
    if (inFlight.current) return;
    inFlight.current = true;
    setRefreshing(true);
    setFailed(null);
    client
      .get("/admin/stats")
      .then(({ data }) => {
        setStats(data);
        setLoadedAt(new Date());
      })
      .catch((e) => setFailed(apiError(e, "The day's numbers didn't load.")))
      .finally(() => {
        inFlight.current = false;
        setRefreshing(false);
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Back at the desk after a while away (another tab, the oven): the
  // figures are fetched again rather than left as they were an hour ago.
  useEffect(() => {
    const back = () => {
      if (document.visibilityState !== "visible" || !loadedAt) return;
      if (Date.now() - loadedAt.getTime() > 60_000) load();
    };
    document.addEventListener("visibilitychange", back);
    return () => document.removeEventListener("visibilitychange", back);
  }, [load, loadedAt]);

  if (failed && !stats) return <LoadError message={failed} onRetry={load} />;
  if (!stats) return <DeskSkeleton />;

  // Money never mixes: the figures speak the shop's active currency, and
  // the split rides alongside whenever any money sits in another one —
  // even a single bucket of history after a cutover must stay visible.
  const currencies = stats.revenue_by_currency ?? [];
  const mixed = hasNonActiveCurrency(currencies.map((c) => c.currency));

  const today = (stats.shop_date ? asDay(stats.shop_date) : new Date()).toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
        <h2 className="font-display text-2xl font-semibold">{today}</h2>
        <div className="flex items-center gap-3">
          <p className="text-xs opacity-70" role="status">
            {failed
              ? "Couldn't refresh — these are the last figures that loaded."
              : loadedAt &&
                `Updated ${loadedAt.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}`}
          </p>
          <button
            type="button"
            className="btn btn-ghost btn-sm border border-base-300"
            onClick={load}
            disabled={refreshing}
          >
            <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} aria-hidden="true" />
            Refresh
          </button>
        </div>
      </div>

      {/* A refresh keeps the page where it is: the last figures stay put,
          dimmed, until the new ones arrive. */}
      <div className={`space-y-6 transition-opacity ${refreshing ? "opacity-60" : ""}`} aria-busy={refreshing}>
        {/* The dated list is the wider of the two: an order number and a
            name need the room a count and a label do not. */}
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[2fr_3fr]">
          <WaitingForYou stats={stats} />
          <DueNext stats={stats} />
        </div>

        <ThirtyDays stats={stats} mixed={mixed} />

        {mixed && (
          <section className={PANEL} aria-labelledby="desk-currencies">
            <h2 id="desk-currencies" className="font-display text-xl font-semibold">Revenue by currency</h2>
            <p className="mt-0.5 text-xs opacity-70">
              Currencies are never combined — each one is its own money.
            </p>
            <dl className="mt-4 grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2 xl:grid-cols-3">
              {currencies.map((c) => (
                <div key={c.currency} className="min-w-0">
                  <dt className="text-xs font-semibold uppercase opacity-70">{c.currency}</dt>
                  <dd className="mt-1 text-xl font-semibold tracking-tight">
                    {money(c.paid_revenue, c.currency)} <span className="text-xs font-normal opacity-70">paid</span>
                  </dd>
                  <dd className="mt-1 text-xs opacity-70">
                    {money(c.total_revenue, c.currency)} total · {money(c.avg_order_value, c.currency)} avg ·{" "}
                    {count(c.order_count, "order")}
                    {c.refunded_revenue > 0 && ` · ${money(c.refunded_revenue, c.currency)} refunded`}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        )}

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1fr_320px]">
          <LatestOrders orders={stats.recent_orders ?? []} />
          <BestSellers products={stats.top_products ?? []} />
        </div>
      </div>
    </div>
  );
}
