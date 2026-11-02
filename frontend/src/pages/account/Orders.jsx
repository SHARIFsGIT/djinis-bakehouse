import { useEffect, useState } from "react";
import { Link } from "react-router";
import client from "../../api/client";
import { formatDate, money, ORDER_STATUSES, statusBadge, statusLabel } from "../../lib/format";
import EmptyState from "../../components/EmptyState";
import { Loading } from "../../components/Loading";

export default function Orders() {
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);

  useEffect(() => {
    setData(null);
    let stale = false;
    const query = { per_page: 10, page };
    if (status) query.status = status;
    client
      .get("/orders", { params: query })
      .then(({ data }) => !stale && setData(data));
    return () => {
      stale = true;
    };
  }, [status, page]);

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

      {data === null ? (
        <Loading label="Checking the order book…" />
      ) : data.orders.length === 0 ? (
        <EmptyState title="No orders here yet" hint="Once you order, every box gets tracked on this shelf.">
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
                <p className="text-xs opacity-60">{formatDate(order.created_at)} · {order.item_count} items</p>
              </div>
              <span className={`badge badge-sm ${statusBadge(order.status)} border`}>
                {statusLabel(order.status)}
              </span>
              <p className="w-20 text-right font-bold">{money(order.total)}</p>
              <span className="text-sm opacity-40" aria-hidden="true">›</span>
            </Link>
          ))}
          {data.pagination.total_pages > 1 && (
            <>
              <div className="flex justify-center pt-2">
                <div className="join">
                  <button
                    type="button"
                    className="btn join-item btn-sm"
                    disabled={data.pagination.page <= 1}
                    onClick={() => setPage(data.pagination.page - 1)}
                  >
                    «
                  </button>
                  <button className="btn join-item btn-sm pointer-events-none">
                    Page {data.pagination.page} / {data.pagination.total_pages}
                  </button>
                  <button
                    type="button"
                    className="btn join-item btn-sm"
                    disabled={data.pagination.page >= data.pagination.total_pages}
                    onClick={() => setPage(data.pagination.page + 1)}
                  >
                    »
                  </button>
                </div>
              </div>
              <p className="pt-2 text-center text-xs opacity-60">
                Showing {data.orders.length} of {data.pagination.total_items} orders
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
