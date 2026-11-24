import { useEffect, useState } from "react";
import { Link } from "react-router";
import { ChevronRight } from "lucide-react";
import client, { apiError } from "../../api/client";
import { formatDate, money, ORDER_STATUSES } from "../../lib/format";
import EmptyState from "../../components/EmptyState";
import { LoadError, Loading } from "../../components/Loading";
import OrderStatusBadge from "../../components/OrderStatusBadge";
import Pager from "../../components/Pager";

export default function Orders() {
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    setData(null);
    setFailed(null);
    let stale = false;
    const query = { per_page: 10, page };
    if (status) query.status = status;
    client
      .get("/orders", { params: query })
      .then(({ data }) => !stale && setData(data))
      // Never a loader that spins forever: say it failed, offer another go.
      .catch((e) => !stale && setFailed(apiError(e, "Your orders didn't load.")));
    return () => {
      stale = true;
    };
  }, [status, page, attempt]);

  const changeStatus = (value) => {
    setStatus(value);
    setPage(1); // a new filter always starts from its first page
  };

  return (
    <div>
      <div className="mb-6 flex flex-wrap gap-2">
        <button
          type="button"
          className={`btn btn-xs ${!status ? "btn-secondary text-secondary-content" : "btn-ghost border border-base-300"}`}
          onClick={() => changeStatus("")}
        >
          All
        </button>
        {ORDER_STATUSES.map((s) => (
          <button
            key={s.value}
            type="button"
            className={`btn btn-xs ${status === s.value ? "btn-secondary text-secondary-content" : "btn-ghost border border-base-300"}`}
            onClick={() => changeStatus(s.value)}
          >
            {s.label}
          </button>
        ))}
      </div>

      {failed ? (
        <LoadError message={failed} onRetry={() => setAttempt((n) => n + 1)} />
      ) : data === null ? (
        <Loading label="Checking the order book…" />
      ) : data.orders.length === 0 ? (
        <EmptyState as="h2" title="No orders here yet" hint="Once you order, every box gets tracked on this shelf.">
          <Link to="/products" className="btn btn-primary btn-sm">Start an order</Link>
        </EmptyState>
      ) : (
        <div className="space-y-3">
          {data.orders.map((order) => (
            <Link
              key={order.id}
              to={`/account/orders/${order.id}`}
              className="flex flex-wrap items-center gap-4 rounded-box glass p-4 shadow-sm ring-1 ring-base-300/60"
            >
              <span className="font-display text-xl font-bold text-secondary" aria-hidden="true">
                {order.order_number.split("-")[1]}
              </span>
              <div className="min-w-40 flex-1">
                <p className="font-display font-bold">{order.order_number}</p>
                <p className="text-xs opacity-70">
                  {formatDate(order.created_at)} · {order.item_count} {order.item_count === 1 ? "item" : "items"}
                </p>
              </div>
              <OrderStatusBadge status={order.status} className="badge-sm" />
              <p className="w-20 text-right font-bold">{money(order.total, order.currency)}</p>
              <ChevronRight size={16} className="shrink-0 opacity-65" aria-hidden="true" />
            </Link>
          ))}
          {data.pagination.total_pages > 1 && (
            <>
              <Pager
                className="pt-2"
                label="Pages of orders"
                page={data.pagination.page}
                pages={data.pagination.total_pages}
                onPage={setPage}
              />
              <p className="pt-2 text-center text-xs opacity-70">
                Showing {data.orders.length} of {data.pagination.total_items} orders
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
