import { ChevronLeft, ChevronRight, SlidersHorizontal, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import client from "../api/client";
import { shelfTitle, titleFor } from "../lib/titles";
import EmptyState from "../components/EmptyState";
import { LoadError, ProductGridSkeleton } from "../components/Loading";
import ProductCard from "../components/ProductCard";
import Sheet from "../components/Sheet";
import { money } from "../lib/format";

const SORTS = [
  { value: "newest", label: "Newest bakes" },
  { value: "name", label: "A to Z" },
  { value: "price_asc", label: "Price: low to high" },
  { value: "price_desc", label: "Price: high to low" },
  { value: "rating", label: "Best rated" },
];

const QUICK_PICKS = [
  { key: "on_sale", label: "On Sale" },
  { key: "in_stock", label: "In stock" },
];

/* The address bar is something people edit, share and mistype. Whatever
   arrives there is read into something the catalog can be asked for — a
   page that isn't a number is page one, a price that isn't a price is no
   price filter — rather than sent on and refused. */
const cleanPage = (raw) => {
  const page = Number.parseInt(raw ?? "", 10);
  return Number.isFinite(page) && page >= 1 ? page : 1;
};
const cleanPrice = (raw) => {
  const value = Number(raw);
  return raw !== null && raw !== "" && Number.isFinite(value) && value >= 0 ? String(value) : "";
};

/** Quick picks — pill buttons that press in when switched on. */
function QuickPicks({ filters, setFilter }) {
  return (
    <div className="flex flex-wrap gap-2">
      {QUICK_PICKS.map(({ key, label }) => {
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
  );
}

/** The price range. `idPrefix` keeps the two copies (sidebar, phone sheet)
    from sharing ids; the form re-mounts when the URL's range changes so its
    boxes always show what is applied. */
function PriceRange({ filters, setFilter, idPrefix }) {
  return (
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
        id={`${idPrefix}-min`}
        name="min"
        type="number"
        inputMode="decimal"
        min="0"
        step="0.5"
        placeholder="Min"
        defaultValue={filters.min_price}
        className="input input-bordered input-sm w-full min-w-0"
        aria-label="Minimum price"
      />
      <span className="text-xs opacity-70">to</span>
      <input
        id={`${idPrefix}-max`}
        name="max"
        type="number"
        inputMode="decimal"
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
  );
}

export default function Products() {
  const [params, setParams] = useSearchParams();
  const [categories, setCategories] = useState([]);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  // null | "missing-shelf" (the URL names a shelf that isn't there) | "failed"
  const [problem, setProblem] = useState(null);
  const [attempt, setAttempt] = useState(0);
  // The phone's filter sheet (a wide screen keeps its filters in the sidebar)
  const [sheetOpen, setSheetOpen] = useState(false);

  // All filter state lives in the URL — shareable and back-button friendly.
  const filters = useMemo(
    () => ({
      category: params.get("category") ?? "",
      search: params.get("search") ?? "",
      sort: SORTS.some((s) => s.value === params.get("sort")) ? params.get("sort") : "newest",
      on_sale: params.get("on_sale") === "true",
      in_stock: params.get("in_stock") === "true",
      min_price: cleanPrice(params.get("min_price")),
      max_price: cleanPrice(params.get("max_price")),
      page: cleanPage(params.get("page")),
    }),
    [params],
  );

  useEffect(() => {
    client
      .get("/products/categories")
      .then(({ data }) => setCategories(data))
      .catch(() => {}); // the shelf list is a convenience — the results below still load
  }, []);

  useEffect(() => {
    setLoading(true);
    setProblem(null);
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
      .then(({ data }) => {
        if (stale) return;
        // A page past the end (a filter narrowed the list, or the link is
        // old): step back to the last page that exists instead of showing
        // an empty shelf beside a count that says otherwise.
        const last = Math.max(1, data.pagination?.total_pages ?? 1);
        if (data.products.length === 0 && filters.page > last) {
          const next = new URLSearchParams(params);
          if (last > 1) next.set("page", last);
          else next.delete("page");
          stale = true; // this answer is never shown — the loader stays up for the next one
          setParams(next, { replace: true });
          return;
        }
        setData(data);
      })
      .catch((error) => {
        if (stale) return;
        // Never leave the previous filter's bakes on screen under this one.
        setData(null);
        setProblem(error?.response?.status === 404 ? "missing-shelf" : "failed");
      })
      .finally(() => !stale && setLoading(false));
    return () => {
      stale = true;
    };
    // `attempt` re-runs the same request for "Try again".
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, attempt]);

  // A new page of results — or a filter that changes them — starts where
  // the results start. Without this the pager at the bottom turned the page
  // somewhere above the screen: the shopper was left looking at the pager
  // (or, after a filter emptied most of the shelf, at the footer). Only
  // when the top of the results has scrolled out of view; near the top of
  // the page nothing moves.
  const results = useRef(null);
  const settled = useRef(false);
  useEffect(() => {
    if (!settled.current) {
      settled.current = true;
      return;
    }
    const box = results.current;
    if (box && box.getBoundingClientRect().top < 0) box.scrollIntoView({ block: "start", behavior: "instant" });
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

  // A shelf names the tab after itself, as the server's head for this
  // address already does (backend/app/seo.py) — the two never disagree.
  useEffect(() => {
    document.title = activeCategory ? shelfTitle(activeCategory.name) : titleFor("/products");
  }, [activeCategory]);

  // Everything narrowing the shelf right now, each with the patch that
  // takes it off again — shown as chips above the results, so what is
  // applied is always in view and one tap from gone.
  const applied = [
    filters.category && {
      key: "category",
      label: activeCategory?.name ?? filters.category,
      clear: { category: "" },
    },
    filters.search && { key: "search", label: `“${filters.search}”`, clear: { search: "" } },
    filters.on_sale && { key: "on_sale", label: "On Sale", clear: { on_sale: false } },
    filters.in_stock && { key: "in_stock", label: "In stock", clear: { in_stock: false } },
    (filters.min_price || filters.max_price) && {
      key: "price",
      label:
        filters.min_price && filters.max_price
          ? `${money(Number(filters.min_price))} – ${money(Number(filters.max_price))}`
          : filters.min_price
            ? `From ${money(Number(filters.min_price))}`
            : `Up to ${money(Number(filters.max_price))}`,
      clear: { min_price: "", max_price: "" },
    },
  ].filter(Boolean);
  // What the phone's "Filters" button counts: the sheet's own two groups.
  const sheetCount =
    Number(filters.on_sale) + Number(filters.in_stock) + Number(Boolean(filters.min_price || filters.max_price));

  const shelfChip = (active) =>
    `btn btn-sm rounded-full whitespace-nowrap ${
      active ? "btn-secondary text-secondary-content" : "btn-ghost border border-base-300"
    }`;

  const total = pagination?.total_items ?? 0;

  return (
    <div className="mx-auto max-w-7xl px-4 py-10">
      {/* Header */}
      <div className="mb-6 lg:mb-8">
        <p className="text-xs font-bold tracking-[0.25em] text-secondary uppercase">
          The full case
        </p>
        <h1 className="font-display mt-2 text-4xl font-semibold text-balance md:text-5xl">
          {activeCategory ? activeCategory.name : "Everything fresh today"}
        </h1>
        {activeCategory?.description && (
          <p className="mt-2 max-w-xl opacity-70">
            {activeCategory.description}
          </p>
        )}
      </div>

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[260px_1fr]">
        {/* Sidebar filters — a wide screen only. It stays in view while the
            shelf scrolls past (sticky under the header), so narrowing the
            list never means scrolling back up for the controls. */}
        <aside className="hidden space-y-6 lg:sticky lg:top-40 lg:block lg:self-start">
          <div>
            <p className="font-display mb-2 font-semibold">Shelves</p>
            <div className="flex flex-col gap-2">
              <button
                type="button"
                onClick={() => setFilter({ category: "" })}
                className={`btn btn-sm w-full ${!filters.category ? "btn-secondary text-secondary-content" : "btn-ghost border border-base-300"}`}
              >
                Everything
              </button>
              {categories.map((c) => (
                <button
                  key={c.slug}
                  type="button"
                  onClick={() => setFilter({ category: c.slug })}
                  className={`btn btn-sm w-full justify-between ${
                    filters.category === c.slug
                      ? "btn-secondary text-secondary-content"
                      : "btn-ghost border border-base-300"
                  }`}
                >
                  {c.name}
                  <span className="badge badge-xs figures mr-1 opacity-70">
                    {c.product_count}
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div className="dash-divider pt-4">
            <p className="font-display mb-2 font-semibold">Quick picks</p>
            <QuickPicks filters={filters} setFilter={setFilter} />
          </div>

          <div className="dash-divider pt-4">
            <p className="font-display mb-2 font-semibold">Price</p>
            <PriceRange filters={filters} setFilter={setFilter} idPrefix="side" />
          </div>
        </aside>

        {/* Results */}
        <div ref={results} className="min-w-0">
          {/* On a phone the shelves are one row to swipe (bleeding to the
              screen's edges, the way a native list does), and everything
              else that narrows the list lives one tap away in a sheet —
              so the first bake is on the first screen, not under a column
              of controls. */}
          <div className="lg:hidden">
            <div className="swipe-row -mx-4 gap-2 px-4 pb-1" role="group" aria-label="Shelves">
              <button
                type="button"
                onClick={() => setFilter({ category: "" })}
                aria-pressed={!filters.category}
                className={shelfChip(!filters.category)}
              >
                Everything
              </button>
              {categories.map((c) => (
                <button
                  key={c.slug}
                  type="button"
                  onClick={() => setFilter({ category: c.slug })}
                  aria-pressed={filters.category === c.slug}
                  className={shelfChip(filters.category === c.slug)}
                >
                  {c.name}
                  <span className="figures text-xs opacity-70">{c.product_count}</span>
                </button>
              ))}
            </div>
            <div className="mt-3 flex items-center gap-2">
              <button
                type="button"
                className="btn btn-sm btn-ghost border border-base-300"
                onClick={() => setSheetOpen(true)}
                aria-haspopup="dialog"
              >
                <SlidersHorizontal size={15} aria-hidden="true" />
                Filters
                {sheetCount > 0 && (
                  <span className="badge badge-secondary badge-xs figures text-secondary-content">
                    {sheetCount}
                  </span>
                )}
              </button>
              <select
                className="select select-bordered select-sm min-w-0 flex-1"
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
          </div>

          <div className="mt-4 mb-4 flex flex-wrap items-center justify-between gap-3 lg:mt-0">
            <p className="text-sm opacity-70" aria-live="polite">
              {loading
                ? "Fetching from the oven…"
                : `${total} ${total === 1 ? "treat" : "treats"}`}
              {filters.search && (
                <>
                  {" "}
                  matching “
                  <span className="font-semibold">{filters.search}</span>”
                </>
              )}
            </p>
            <select
              className="select select-bordered select-sm hidden w-56 lg:block"
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

          {applied.length > 0 && (
            <ul className="mb-4 flex flex-wrap items-center gap-2" aria-label="Filters applied">
              {applied.map((chip) => (
                <li key={chip.key}>
                  <button
                    type="button"
                    onClick={() => setFilter(chip.clear)}
                    className="flex items-center gap-1.5 rounded-full bg-base-200 py-1 pr-2 pl-3 text-sm font-semibold transition hover:bg-base-300"
                    aria-label={`Remove the filter ${chip.label}`}
                  >
                    {chip.label}
                    <X size={14} className="opacity-70" aria-hidden="true" />
                  </button>
                </li>
              ))}
              {applied.length > 1 && (
                <li>
                  <button
                    type="button"
                    className="link link-secondary px-1 text-sm font-semibold"
                    onClick={() => setParams(new URLSearchParams())}
                  >
                    Clear all
                  </button>
                </li>
              )}
            </ul>
          )}

          {loading ? (
            <ProductGridSkeleton className="grid-cols-2 md:grid-cols-3" count={6} />
          ) : problem === "missing-shelf" ? (
            <EmptyState
              as="h2"
              title="That shelf isn't here"
              hint="The link points at a shelf the bakery no longer keeps — everything else is still on the counter."
            >
              <button
                type="button"
                className="btn btn-secondary btn-sm text-secondary-content"
                onClick={() => setParams(new URLSearchParams())}
              >
                See everything
              </button>
            </EmptyState>
          ) : problem === "failed" ? (
            <LoadError
              message="The shelves didn't load — check your connection and try again."
              onRetry={() => setAttempt((n) => n + 1)}
            />
          ) : data?.products?.length ? (
            <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
              {/* The first row is what the shelf opens on: its photos are
                  fetched first, ahead of the cards further down. */}
              {data.products.map((p, i) => (
                <ProductCard key={p.id} product={p} eager={i < 2} />
              ))}
            </div>
          ) : (
            <EmptyState
              as="h2"
              title="The shelf is bare"
              hint="Nothing matches those filters — try loosening them a little."
            >
              {applied.length > 0 && (
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
              <div className="join" role="navigation" aria-label="Pages of bakes">
                <button
                  type="button"
                  className="btn join-item btn-sm"
                  disabled={pagination.page <= 1}
                  aria-label="Previous page"
                  onClick={() =>
                    setFilter({ page: pagination.page - 1 }, false)
                  }
                >
                  <ChevronLeft size={16} aria-hidden="true" />
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
                        className={`join-item btn btn-sm figures ${p === pagination.page ? "btn-primary" : ""}`}
                        aria-label={`Page ${p}`}
                        aria-current={p === pagination.page ? "page" : undefined}
                      >
                        {p}
                      </button>
                    </span>
                  ))}
                <button
                  type="button"
                  className="btn join-item btn-sm"
                  disabled={pagination.page >= pagination.total_pages}
                  aria-label="Next page"
                  onClick={() =>
                    setFilter({ page: pagination.page + 1 }, false)
                  }
                >
                  <ChevronRight size={16} aria-hidden="true" />
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {sheetOpen && (
        <Sheet
          title="Filters"
          onClose={() => setSheetOpen(false)}
          footer={
            <button type="button" className="btn btn-primary w-full" onClick={() => setSheetOpen(false)}>
              {loading ? "Show the bakes" : `Show ${total} ${total === 1 ? "bake" : "bakes"}`}
            </button>
          }
        >
          <p className="font-display mb-2 font-semibold">Quick picks</p>
          <QuickPicks filters={filters} setFilter={setFilter} />
          <p className="font-display mt-6 mb-2 font-semibold">Price</p>
          <PriceRange filters={filters} setFilter={setFilter} idPrefix="sheet" />
        </Sheet>
      )}
    </div>
  );
}
