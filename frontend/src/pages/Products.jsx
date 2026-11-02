import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import client from "../api/client";
import ProductCard from "../components/ProductCard";
import EmptyState from "../components/EmptyState";
import { ProductGridSkeleton } from "../components/Loading";

const SORTS = [
  { value: "newest", label: "Newest bakes" },
  { value: "name", label: "A → Z" },
  { value: "price_asc", label: "Price: low → high" },
  { value: "price_desc", label: "Price: high → low" },
  { value: "rating", label: "Best rated" },
];

export default function Products() {
  const [params, setParams] = useSearchParams();
  const [categories, setCategories] = useState([]);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  // All filter state lives in the URL — shareable and back-button friendly.
  const filters = useMemo(
    () => ({
      category: params.get("category") ?? "",
      search: params.get("search") ?? "",
      sort: params.get("sort") ?? "newest",
      on_sale: params.get("on_sale") === "true",
      in_stock: params.get("in_stock") === "true",
      min_price: params.get("min_price") ?? "",
      max_price: params.get("max_price") ?? "",
      page: Number(params.get("page") ?? 1),
    }),
    [params],
  );

  useEffect(() => {
    client.get("/products/categories").then(({ data }) => setCategories(data));
  }, []);

  useEffect(() => {
    setLoading(true);
    let stale = false;
    const query = {
      sort: filters.sort,
      page: filters.page,
      per_page: 12,
    };
    if (filters.category) query.category = filters.category;
    if (filters.search) query.search = filters.search;
    if (filters.on_sale) query.on_sale = true;
    if (filters.in_stock) query.in_stock = true;
    if (filters.min_price) query.min_price = filters.min_price;
    if (filters.max_price) query.max_price = filters.max_price;

    client
      .get("/products", { params: query })
      .then(({ data }) => !stale && setData(data))
      .finally(() => !stale && setLoading(false));
    return () => {
      stale = true;
    };
  }, [filters]);

  const setFilter = (patch, resetPage = true) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(patch)) {
      if (value === "" || value === false || value === null) next.delete(key);
      else next.set(key, value);
    }
    if (resetPage) next.delete("page");
    setParams(next, { preventScrollReset: false });
  };

  const activeCategory = categories.find((c) => c.slug === filters.category);
  const pagination = data?.pagination;
  const hasActiveFilters =
    filters.category ||
    filters.search ||
    filters.on_sale ||
    filters.in_stock ||
    filters.min_price ||
    filters.max_price;

  return (
    <div className="mx-auto max-w-7xl px-4 py-10">
      {/* Header */}
      <div className="mb-8">
        <p className="text-xs font-bold tracking-[0.25em] text-secondary uppercase">
          The full case
        </p>
        <h1 className="font-display mt-2 text-4xl font-semibold md:text-5xl">
          {activeCategory ? activeCategory.name : "Everything fresh today"}
        </h1>
        {activeCategory?.description && (
          <p className="mt-2 max-w-xl opacity-70">
            {activeCategory.description}
          </p>
        )}
      </div>

      <div className="grid gap-8 lg:grid-cols-[260px_1fr]">
        {/* Sidebar filters */}
        <aside className="space-y-6">
          <div>
            <p className="font-display mb-2 font-semibold">Shelves</p>
            <div className="flex flex-wrap gap-2 lg:flex-col">
              <button
                type="button"
                onClick={() => setFilter({ category: "" })}
                className={`btn btn-sm lg:w-full ${!filters.category ? "btn-secondary text-secondary-content" : "btn-ghost border border-base-300"}`}
              >
                Everything
              </button>
              {categories.map((c) => (
                <button
                  key={c.slug}
                  type="button"
                  onClick={() => setFilter({ category: c.slug })}
                  className={`btn btn-sm justify-start lg:w-full lg:justify-between ${
                    filters.category === c.slug
                      ? "btn-secondary text-secondary-content"
                      : "btn-ghost border border-base-300"
                  }`}
                >
                  {c.name}
                  <span className="badge badge-xs mr-1 ml-auto lg:ml-0 opacity-60">
                    {c.product_count}
                  </span>
                </button>
              ))}
            </div>
          </div>

          {/* Quick picks — pill buttons that press in when switched on */}
          <div className="dash-divider pt-4">
            <p className="font-display mb-2 font-semibold">Quick picks</p>
            <div className="flex flex-wrap gap-2">
              {[
                { key: "on_sale", label: "On sale" },
                { key: "in_stock", label: "In stock" },
              ].map(({ key, label }) => {
                const on = Boolean(filters[key]);
                return (
                  <button
                    key={key}
                    type="button"
                    aria-pressed={on}
                    onClick={() => setFilter({ [key]: !on })}
                    className={`rounded-full border px-3.5 py-1.5 text-sm font-semibold transition ${
                      on
                        ? "border-base-300 bg-base-300 shadow-sm"
                        : "border-base-300 hover:bg-base-200"
                    }`}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="dash-divider pt-4">
            <p className="font-display mb-2 font-semibold">Price</p>
            <form
              key={`${filters.min_price}|${filters.max_price}`}
              className="flex items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                const form = new FormData(e.target);
                setFilter({
                  min_price: form.get("min") ?? "",
                  max_price: form.get("max") ?? "",
                });
              }}
            >
              <input
                name="min"
                type="number"
                min="0"
                step="0.5"
                placeholder="Min"
                defaultValue={filters.min_price}
                className="input input-bordered input-sm w-full min-w-0"
                aria-label="Minimum price"
              />
              <span className="text-xs opacity-50">to</span>
              <input
                name="max"
                type="number"
                min="0"
                step="0.5"
                placeholder="Max"
                defaultValue={filters.max_price}
                className="input input-bordered input-sm w-full min-w-0"
                aria-label="Maximum price"
              />
              <button type="submit" className="btn btn-primary btn-sm">
                Go
              </button>
            </form>
          </div>
        </aside>

        {/* Results */}
        <div>
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm opacity-70">
              {loading
                ? "Fetching from the oven…"
                : `${pagination?.total_items ?? 0} ${pagination?.total_items === 1 ? "treat" : "treats"}`}
              {filters.search && (
                <>
                  {" "}
                  matching “
                  <span className="font-semibold">{filters.search}</span>”
                </>
              )}
            </p>
            <select
              className="select select-bordered select-sm"
              value={filters.sort}
              onChange={(e) => setFilter({ sort: e.target.value })}
              aria-label="Sort products"
            >
              {SORTS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>

          {loading ? (
            <ProductGridSkeleton />
          ) : data?.products?.length ? (
            <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
              {data.products.map((p) => (
                <ProductCard key={p.id} product={p} />
              ))}
            </div>
          ) : (
            <EmptyState
              title="The shelf is bare"
              hint="Nothing matches those filters — try loosening them a little."
            >
              {hasActiveFilters && (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm text-secondary-content"
                  onClick={() => setParams(new URLSearchParams())}
                >
                  Clear all filters
                </button>
              )}
            </EmptyState>
          )}

          {/* Pagination */}
          {pagination && pagination.total_pages > 1 && (
            <div className="mt-8 flex items-center justify-center gap-2">
              <div className="join">
                <button
                  type="button"
                  className="btn join-item btn-sm"
                  disabled={pagination.page <= 1}
                  onClick={() =>
                    setFilter({ page: pagination.page - 1 }, false)
                  }
                >
                  «
                </button>
                {Array.from({ length: pagination.total_pages }, (_, i) => i + 1)
                  .filter(
                    (p) =>
                      p === 1 ||
                      p === pagination.total_pages ||
                      Math.abs(p - pagination.page) <= 1,
                  )
                  .map((p, idx, arr) => (
                    <span key={p} className="flex">
                      {idx > 0 && p - arr[idx - 1] > 1 && (
                        <span className="btn btn-disabled join-item btn-sm pointer-events-none">
                          …
                        </span>
                      )}
                      <button
                        type="button"
                        onClick={() => setFilter({ page: p }, false)}
                        className={`join-item btn btn-sm ${p === pagination.page ? "btn-primary" : ""}`}
                      >
                        {p}
                      </button>
                    </span>
                  ))}
                <button
                  type="button"
                  className="btn join-item btn-sm"
                  disabled={pagination.page >= pagination.total_pages}
                  onClick={() =>
                    setFilter({ page: pagination.page + 1 }, false)
                  }
                >
                  »
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
