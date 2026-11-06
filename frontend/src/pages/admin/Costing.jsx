import { useEffect, useRef, useState } from "react";
import { History, Plus, Trash2 } from "lucide-react";
import client, { apiError } from "../../api/client";
import { formatDate, money } from "../../lib/format";
import {
  REVIEW_REASONS,
  UNIT_OPTIONS,
  costStatusMeta,
  overheadRatePerHour,
} from "../../lib/costing";
import { useToast } from "../../context/ToastContext";
import ConfirmDialog from "../../components/ConfirmDialog";
import { Loading } from "../../components/Loading";

/* The Phase 2 costing desk — the UI over the /api/admin/costing surface.
   Five tabs, the way the work actually runs: what needs review, the
   ingredient book, recipes per product, production batches, and the two
   shop-wide figures (overhead months, labour rate). Everything derives at
   read; every write lands append-only where the backend says so. */

const TABS = [
  { key: "review", label: "Review" },
  { key: "ingredients", label: "Ingredients" },
  { key: "recipes", label: "Recipes" },
  { key: "batches", label: "Batches" },
  { key: "overhead", label: "Overhead & labour" },
];

const CONFIDENCE = ["high", "medium", "low"];
// EUR-only since the 2026-11-05 cutover — the backend refuses any other
// currency on write, so the book offers none.
const CURRENCIES = ["eur"];

const EMPTY_INGREDIENT = {
  name: "",
  category_id: "",
  unit: "kg",
  purchase_qty: "",
  purchase_price: "",
  currency: "eur",
  confidence: "medium",
  source: "",
};

const EMPTY_RECIPE = {
  hands_on_minutes: "",
  unattended_minutes: "",
  expected_batch_yield: "",
  batch_overhead_estimate: "",
  confidence: "medium",
  notes: "",
  items: [],
};

const EMPTY_BATCH = {
  product_id: "",
  produced_at: "",
  expected_yield: "",
  batch_overhead_estimated: "",
  overhead_categories: "",
  usage_notes: "",
};

const EMPTY_MONTH = {
  month: "",
  fixed_overhead_total: "",
  forecast_hours: "",
  currency: "eur",
  included_categories: "",
  source: "",
  confidence: "medium",
};

const EMPTY_RATE = { valid_from: "", rate: "", currency: "eur", source: "" };

const missingLabel = (code) =>
  String(code).replace(/_/g, " ").replace(/:/, " — ");

function StatusBadge({ status }) {
  const meta = costStatusMeta(status);
  return <span className={`badge badge-sm border ${meta.badge}`}>{meta.label}</span>;
}

function EstBadge({ isEstimate }) {
  if (!isEstimate) return null;
  return (
    <span className="badge badge-ghost badge-sm border align-middle ml-1" title="Figures not confirmed by a review yet">
      est.
    </span>
  );
}

function Card({ children, className = "" }) {
  return (
    <div className={`rounded-box glass p-4 shadow-sm ring-1 ring-base-300/60 ${className}`}>
      {children}
    </div>
  );
}

function Field({ label, children }) {
  return (
    <div className="min-w-0">
      <label className="label text-xs">{label}</label>
      {children}
    </div>
  );
}

const inputCls = "input input-bordered input-sm w-full";
const selectCls = "select select-bordered select-sm w-full";

export default function Costing() {
  const toast = useToast();
  const [tab, setTab] = useState("review");
  const [products, setProducts] = useState(null);
  const [recipeProductId, setRecipeProductId] = useState(null);

  const loadProducts = async () => {
    // The pickers want the whole shelf; the API pages at 48.
    const all = [];
    let page = 1;
    for (;;) {
      const { data } = await client.get("/admin/products", { params: { page, per_page: 48 } });
      all.push(...(data.products ?? []));
      if (page >= (data.pagination?.total_pages ?? 1) || page > 20) break;
      page += 1;
    }
    setProducts(all);
  };

  useEffect(() => {
    loadProducts().catch((e) => toast.error(apiError(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openRecipeFor = (productId) => {
    setRecipeProductId(productId);
    setTab("recipes");
  };

  return (
    <div className="space-y-6">
      {/* Tabs */}
      <div className="flex flex-wrap items-center gap-2">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={`btn btn-sm rounded-full font-semibold ${
              tab === t.key
                ? "btn-secondary text-secondary-content"
                : "btn-ghost border border-base-300"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "review" && <ReviewTab onOpenRecipe={openRecipeFor} />}
      {tab === "ingredients" && <IngredientsTab />}
      {tab === "recipes" && (
        <RecipesTab products={products} selectedId={recipeProductId} onSelect={setRecipeProductId} />
      )}
      {tab === "batches" && <BatchesTab products={products} />}
      {tab === "overhead" && <OverheadTab />}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Review — what needs attention, derived at read                      */
/* ------------------------------------------------------------------ */

function ReviewTab({ onOpenRecipe }) {
  const toast = useToast();
  const [data, setData] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [windowDays, setWindowDays] = useState(14);

  useEffect(() => {
    // The old shelf stays up (dimmed) while the new window loads — swapping
    // it for a spinner would bounce the whole tab on every change.
    setRefreshing(true);
    client
      .get("/admin/costing/cost-review", { params: { window: windowDays } })
      .then(({ data }) => setData(data))
      .catch((e) => toast.error(apiError(e)))
      .finally(() => setRefreshing(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [windowDays]);

  if (data === null) return <Loading label="Checking the proof…" />;

  const { overdue, upcoming, overlap_warnings: warnings } = data;

  const entryRow = (entry) => {
    const cost = entry.cost;
    return (
      <div
        key={`${entry.reason}-${entry.product_id}`}
        className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-xl bg-base-200/50 px-4 py-3"
      >
        <div className="min-w-0 flex-1">
          <p className="font-semibold">{entry.product_name}</p>
          <p className="text-xs opacity-60">
            {REVIEW_REASONS[entry.reason] ?? entry.reason}
            {entry.next_reviewed && ` · review due ${formatDate(entry.next_reviewed)}`}
          </p>
        </div>
        <StatusBadge status={entry.cost_status} />
        {cost?.total_cost != null && (
          <span className="text-sm font-bold">
            {money(cost.total_cost, cost.currency)}
            <span className="ml-1 text-xs font-normal opacity-60">per unit</span>
          </span>
        )}
        <button
          type="button"
          className="btn btn-ghost btn-xs"
          onClick={() => onOpenRecipe(entry.product_id)}
        >
          Open recipe
        </button>
      </div>
    );
  };

  return (
    <div className="space-y-6">
      {warnings.length > 0 && (
        <Card className="border-l-4 border-warning">
          <h3 className="font-display text-base font-semibold">Overhead double-count warning</h3>
          <ul className="mt-1 list-inside list-disc text-sm opacity-80">
            {warnings.map((w, i) => (
              <li key={i}>
                {w.month}: {w.overlapping_categories.join(", ")} — also recorded on
                batch #{w.batch_id}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className={refreshing ? "space-y-6 opacity-60 transition-opacity" : "space-y-6 transition-opacity"}>
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-display text-lg font-semibold">
            Overdue ({overdue.length})
            {refreshing && <span className="loading loading-spinner loading-xs ml-2 align-middle opacity-60" />}
          </h3>
          <label className="flex items-center gap-2 text-xs opacity-70">
            Upcoming window
            <select
              className="select select-bordered select-xs"
              value={windowDays}
              onChange={(e) => setWindowDays(Number(e.target.value))}
            >
              {[7, 14, 30, 90].map((d) => (
                <option key={d} value={d}>
                  {d} days
                </option>
              ))}
            </select>
          </label>
        </div>
        {overdue.length === 0 ? (
          <p className="mt-3 text-sm opacity-60">Nothing overdue — the book is current.</p>
        ) : (
          <div className="mt-3 space-y-2">{overdue.map(entryRow)}</div>
        )}
      </Card>

      <Card>
        <h3 className="font-display text-lg font-semibold">Upcoming ({upcoming.length})</h3>
        {upcoming.length === 0 ? (
          <p className="mt-3 text-sm opacity-60">No reviews fall due in the next {windowDays} days.</p>
        ) : (
          <div className="mt-3 space-y-2">{upcoming.map(entryRow)}</div>
        )}
      </Card>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Ingredients — the price book                                        */
/* ------------------------------------------------------------------ */

const EDITABLE_FIELDS = [
  "name",
  "category_id",
  "unit",
  "purchase_qty",
  "purchase_price",
  "currency",
  "density_g_per_ml",
  "confidence",
  "source",
  "invoice_ref",
  "review_interval_days",
];
const OPTIONAL_ON_EDIT = [
  "category_id",
  "density_g_per_ml",
  "source",
  "invoice_ref",
  "review_interval_days",
];
const NUMERIC_EDIT_FIELDS = [
  "category_id",
  "purchase_qty",
  "purchase_price",
  "density_g_per_ml",
  "review_interval_days",
];

function IngredientsTab() {
  const toast = useToast();
  const [data, setData] = useState(null);
  const seq = useRef(0); // keystroke-race guard: only the latest fetch may paint
  const [categories, setCategories] = useState([]);
  const [catsOpen, setCatsOpen] = useState(false);
  const [catForm, setCatForm] = useState({ name: "", review_interval_days: "90" });
  const [busyCat, setBusyCat] = useState(false);
  const [editingCatId, setEditingCatId] = useState(null);
  const [catEdit, setCatEdit] = useState({});
  const [deletingCat, setDeletingCat] = useState(null);
  const [filters, setFilters] = useState({ search: "", category_id: "", overdue: false });
  const [page, setPage] = useState(1);
  const [form, setForm] = useState(EMPTY_INGREDIENT);
  const [busy, setBusy] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editDraft, setEditDraft] = useState({});
  const [historyFor, setHistoryFor] = useState(null);
  const [history, setHistory] = useState(null);
  const [deleting, setDeleting] = useState(null);

  const loadCategories = () =>
    client.get("/admin/costing/ingredient-categories").then(({ data }) => setCategories(data));

  const load = () => {
    const ticket = ++seq.current;
    const params = {
      page,
      per_page: 15,
      overdue: filters.overdue || undefined,
      category_id: filters.category_id || undefined,
      search: filters.search.trim() || undefined,
    };
    return client.get("/admin/costing/ingredients", { params }).then(({ data }) => {
      if (ticket === seq.current) setData(data);
    });
  };

  useEffect(() => {
    loadCategories().catch((e) => toast.error(apiError(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    load().catch((e) => toast.error(apiError(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, filters]);

  const create = async (e) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      await client.post("/admin/costing/ingredients", {
        name: form.name.trim(),
        category_id: form.category_id ? Number(form.category_id) : null,
        unit: form.unit,
        purchase_qty: Number(form.purchase_qty),
        purchase_price: Number(form.purchase_price),
        currency: form.currency.trim().toLowerCase(),
        confidence: form.confidence,
        source: form.source.trim() || null,
      });
      toast.success(`${form.name.trim()} is in the book`);
      setForm(EMPTY_INGREDIENT);
      setPage(1);
      await load();
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setBusy(false);
    }
  };

  const createCategory = async (e) => {
    e.preventDefault();
    if (busyCat) return;
    setBusyCat(true);
    try {
      const { data } = await client.post("/admin/costing/ingredient-categories", {
        name: catForm.name.trim(),
        review_interval_days: Number(catForm.review_interval_days || 90),
      });
      toast.success(`Category "${data.name}" added — members default to a ${data.review_interval_days}-day review`);
      setCatForm({ name: "", review_interval_days: "90" });
      await loadCategories();
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setBusyCat(false);
    }
  };

  const saveCatEdit = async (cat) => {
    try {
      await client.put(`/admin/costing/ingredient-categories/${cat.id}`, {
        name: catEdit.name.trim(),
        review_interval_days: Number(catEdit.review_interval_days),
      });
      toast.success("Category updated");
      setEditingCatId(null);
      await loadCategories();
      await load();
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  const confirmDeleteCat = async () => {
    try {
      await client.delete(`/admin/costing/ingredient-categories/${deletingCat.id}`);
      toast.info(`Category "${deletingCat.name}" removed`);
      setDeletingCat(null);
      await loadCategories();
    } catch (err) {
      toast.error(apiError(err));
      setDeletingCat(null);
    }
  };

  const startEdit = (ing) => {
    setHistoryFor(null);
    setEditingId(ing.id);
    setEditDraft({
      name: ing.name,
      category_id: ing.category_id ?? "",
      unit: ing.unit,
      purchase_qty: String(ing.purchase_qty),
      purchase_price: String(ing.purchase_price),
      currency: ing.currency,
      density_g_per_ml: ing.density_g_per_ml == null ? "" : String(ing.density_g_per_ml),
      confidence: ing.confidence,
      source: ing.source ?? "",
      invoice_ref: ing.invoice_ref ?? "",
      review_interval_days: ing.review_interval_days ?? "",
    });
  };

  const saveEdit = async (ing) => {
    const payload = {};
    for (const key of EDITABLE_FIELDS) {
      const value = editDraft[key];
      if (value === "" || value === null) {
        // An emptied optional field clears the stored value; name and the
        // purchase figures simply stay out of the payload (untouched).
        if (OPTIONAL_ON_EDIT.includes(key)) payload[key] = null;
        continue;
      }
      payload[key] = NUMERIC_EDIT_FIELDS.includes(key) ? Number(value) : value;
    }
    try {
      await client.put(`/admin/costing/ingredients/${ing.id}`, payload);
      toast.success(`${ing.name} updated`);
      setEditingId(null);
      await load();
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  const markReviewed = async (ing) => {
    try {
      const { data } = await client.post(`/admin/costing/ingredients/${ing.id}/review`, {});
      toast.success(
        `${data.name} figures confirmed — next review ${formatDate(data.next_reviewed)}`,
      );
      await load();
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  const toggleHistory = async (ing) => {
    if (historyFor === ing.id) {
      setHistoryFor(null);
      return;
    }
    setEditingId(null);
    setHistoryFor(ing.id);
    setHistory(null);
    try {
      const { data } = await client.get(`/admin/costing/ingredients/${ing.id}/price-events`);
      setHistory(data);
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  const confirmDelete = async () => {
    try {
      await client.delete(`/admin/costing/ingredients/${deleting.id}`);
      toast.info(`${deleting.name} removed`);
      setDeleting(null);
      await load();
    } catch (err) {
      toast.error(apiError(err));
      setDeleting(null);
    }
  };

  const ingredients = data?.ingredients ?? [];
  const pagination = data?.pagination;

  return (
    <div className="space-y-6">
      {/* Create — two-up on tablets, one row only where it genuinely fits */}
      <form
        onSubmit={create}
        className="grid gap-3 rounded-box glass p-4 shadow-sm ring-1 ring-base-300/60 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,90px)_minmax(0,100px)_minmax(0,100px)_minmax(0,100px)_minmax(0,1.2fr)_auto] xl:items-end"
      >
        <Field label="Name *">
          <input
            className={inputCls}
            required
            maxLength={150}
            placeholder="Stone-ground wheat flour"
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          />
        </Field>
        <Field label="Category">
          <select
            className={selectCls}
            value={form.category_id}
            onChange={(e) => setForm((f) => ({ ...f, category_id: e.target.value }))}
          >
            <option value="">—</option>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Unit *">
          <select
            className={selectCls}
            value={form.unit}
            onChange={(e) => setForm((f) => ({ ...f, unit: e.target.value }))}
          >
            {UNIT_OPTIONS.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Qty *">
          <input
            type="number"
            className={inputCls}
            required
            min="0.001"
            step="any"
            placeholder="25"
            value={form.purchase_qty}
            onChange={(e) => setForm((f) => ({ ...f, purchase_qty: e.target.value }))}
          />
        </Field>
        <Field label="Price *">
          <input
            type="number"
            className={inputCls}
            required
            min="0"
            step="any"
            placeholder="37.50"
            value={form.purchase_price}
            onChange={(e) => setForm((f) => ({ ...f, purchase_price: e.target.value }))}
          />
        </Field>
        <Field label="Currency">
          <select
            className={selectCls}
            value={form.currency}
            onChange={(e) => setForm((f) => ({ ...f, currency: e.target.value }))}
          >
            {CURRENCIES.map((c) => (
              <option key={c} value={c}>
                {c.toUpperCase()}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Source">
          <input
            className={inputCls}
            maxLength={1000}
            placeholder="Müller invoice 2026-11"
            value={form.source}
            onChange={(e) => setForm((f) => ({ ...f, source: e.target.value }))}
          />
        </Field>
        <button type="submit" className="btn btn-primary btn-sm min-w-28" disabled={busy}>
          {busy && <span className="loading loading-spinner loading-xs" />}
          Add
        </button>
      </form>

      {/* Categories — the review-interval defaults */}
      {catsOpen && (
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-display text-base font-semibold">Ingredient categories</h3>
            <p className="text-xs opacity-50">
              A category sets the default review cadence its members inherit.
            </p>
          </div>
          <form
            onSubmit={createCategory}
            className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_120px_auto] sm:items-end"
          >
            <Field label="Name *">
              <input
                className={inputCls}
                required
                maxLength={100}
                placeholder="Flours"
                value={catForm.name}
                onChange={(e) => setCatForm((f) => ({ ...f, name: e.target.value }))}
              />
            </Field>
            <Field label="Review interval (days) *">
              <input
                type="number"
                className={inputCls}
                required
                min="1"
                max="3650"
                value={catForm.review_interval_days}
                onChange={(e) => setCatForm((f) => ({ ...f, review_interval_days: e.target.value }))}
              />
            </Field>
            <button type="submit" className="btn btn-primary btn-sm min-w-28" disabled={busyCat}>
              {busyCat && <span className="loading loading-spinner loading-xs" />}
              Add category
            </button>
          </form>
          {categories.length > 0 && (
            <div className="mt-3 overflow-x-auto">
              <table className="table table-sm">
                <thead>
                  <tr className="text-xs uppercase opacity-50">
                    <th>Category</th>
                    <th className="text-right">Review interval</th>
                    <th className="text-right">Ingredients</th>
                    <th className="text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {categories.map((cat) =>
                    editingCatId === cat.id ? (
                      <tr key={cat.id} className="bg-base-200/40">
                        <td>
                          <input
                            className={inputCls}
                            maxLength={100}
                            value={catEdit.name}
                            onChange={(e) => setCatEdit((d) => ({ ...d, name: e.target.value }))}
                          />
                        </td>
                        <td>
                          <input
                            type="number"
                            min="1"
                            max="3650"
                            className={`${inputCls} w-24`}
                            value={catEdit.review_interval_days}
                            onChange={(e) =>
                              setCatEdit((d) => ({ ...d, review_interval_days: e.target.value }))
                            }
                          />
                        </td>
                        <td className="text-right text-sm opacity-60">{cat.ingredient_count}</td>
                        <td className="text-right whitespace-nowrap">
                          <button
                            type="button"
                            className="btn btn-secondary btn-xs"
                            onClick={() => saveCatEdit(cat)}
                          >
                            Save
                          </button>
                          <button
                            type="button"
                            className="btn btn-ghost btn-xs ml-1"
                            onClick={() => setEditingCatId(null)}
                          >
                            Cancel
                          </button>
                        </td>
                      </tr>
                    ) : (
                      <tr key={cat.id}>
                        <td className="font-semibold">{cat.name}</td>
                        <td className="text-right text-sm">{cat.review_interval_days} days</td>
                        <td className="text-right text-sm">{cat.ingredient_count}</td>
                        <td className="text-right whitespace-nowrap">
                          <button
                            type="button"
                            className="btn btn-ghost btn-xs"
                            onClick={() => {
                              setEditingCatId(cat.id);
                              setCatEdit({
                                name: cat.name,
                                review_interval_days: String(cat.review_interval_days),
                              });
                            }}
                          >
                            Edit
                          </button>
                          <button
                            type="button"
                            className="btn btn-ghost btn-xs text-error ml-1"
                            onClick={() => setDeletingCat(cat)}
                          >
                            <Trash2 size={14} />
                          </button>
                        </td>
                      </tr>
                    ),
                  )}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => setCatsOpen((o) => !o)}
          className={`btn btn-xs rounded-full font-semibold ${
            catsOpen ? "btn-secondary" : "btn-ghost border border-base-300"
          }`}
        >
          Categories ({categories.length})
        </button>
        <input
          className="input input-bordered input-sm w-56"
          placeholder="Search name or invoice…"
          value={filters.search}
          onChange={(e) => {
            setPage(1);
            setFilters((f) => ({ ...f, search: e.target.value }));
          }}
          aria-label="Search ingredients"
        />
        <select
          className="select select-bordered select-sm"
          value={filters.category_id}
          onChange={(e) => {
            setPage(1);
            setFilters((f) => ({ ...f, category_id: e.target.value }));
          }}
          aria-label="Filter by category"
        >
          <option value="">All categories</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} ({c.ingredient_count})
            </option>
          ))}
        </select>
        <label className="label cursor-pointer gap-2 text-xs">
          <input
            type="checkbox"
            className="toggle toggle-secondary toggle-sm"
            checked={filters.overdue}
            onChange={(e) => {
              setPage(1);
              setFilters((f) => ({ ...f, overdue: e.target.checked }));
            }}
          />
          Review overdue only
        </label>
      </div>

      {/* List */}
      {data === null ? (
        <Loading label="Weighing the ingredients…" />
      ) : ingredients.length === 0 ? (
        <p className="rounded-box glass p-8 text-center text-sm opacity-60 ring-1 ring-base-300/60">
          No ingredients match — add the first one above.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-box glass shadow-sm ring-1 ring-base-300/60">
          <table className="table table-sm">
            <thead>
              <tr className="text-xs uppercase opacity-50">
                <th>Ingredient</th>
                <th>Purchase</th>
                <th className="text-right">Per unit</th>
                <th>Review</th>
                <th className="text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {ingredients.map((ing) => (
                <IngredientRow
                  key={ing.id}
                  ing={ing}
                  categories={categories}
                  editing={editingId === ing.id}
                  editDraft={editDraft}
                  setEditDraft={setEditDraft}
                  onEdit={() => startEdit(ing)}
                  onCancel={() => setEditingId(null)}
                  onSave={() => saveEdit(ing)}
                  onReview={() => markReviewed(ing)}
                  historyOpen={historyFor === ing.id}
                  history={history}
                  onHistory={() => toggleHistory(ing)}
                  onDelete={() => setDeleting(ing)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {pagination && pagination.total_pages > 1 && (
        <div className="flex items-center justify-between text-xs opacity-70">
          <span>
            Page {pagination.page} of {pagination.total_pages} · {pagination.total_items} ingredients
          </span>
          <span className="flex gap-2">
            <button
              type="button"
              className="btn btn-ghost btn-xs"
              disabled={pagination.page <= 1}
              onClick={() => setPage((p) => p - 1)}
            >
              Prev
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-xs"
              disabled={pagination.page >= pagination.total_pages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </button>
          </span>
        </div>
      )}

      {deleting && (
        <ConfirmDialog
          title={`Remove ${deleting.name}?`}
          confirmLabel="Yes, remove it"
          onConfirm={confirmDelete}
          onCancel={() => setDeleting(null)}
        >
          Its price history goes with it. Recipes using it keep their recorded lines but can no longer recompute.
        </ConfirmDialog>
      )}

      {deletingCat && (
        <ConfirmDialog
          title={`Remove category "${deletingCat.name}"?`}
          confirmLabel="Yes, remove it"
          onConfirm={confirmDeleteCat}
          onCancel={() => setDeletingCat(null)}
        >
          Only possible while no ingredient belongs to it — move them first.
        </ConfirmDialog>
      )}
    </div>
  );
}

function IngredientRow({
  ing,
  categories,
  editing,
  editDraft,
  setEditDraft,
  onEdit,
  onCancel,
  onSave,
  onReview,
  historyOpen,
  history,
  onHistory,
  onDelete,
}) {
  const set = (key) => (e) => setEditDraft((d) => ({ ...d, [key]: e.target.value }));
  return (
    <>
      <tr className={ing.overdue ? "bg-warning/5 hover:bg-base-200/50" : "hover:bg-base-200/50"}>
        <td>
          <span className="font-semibold">
            {ing.name}
            <EstBadge isEstimate={ing.is_estimate} />
          </span>
          {ing.category_name && (
            <span className="block text-xs opacity-50">{ing.category_name}</span>
          )}
        </td>
        <td className="whitespace-nowrap text-sm">
          {ing.purchase_qty} {ing.unit} @ {money(ing.purchase_price, ing.currency)}
        </td>
        <td className="text-right font-bold">
          {ing.effective_cost_per_unit == null
            ? "—"
            : `${money(ing.effective_cost_per_unit, ing.currency)}/${ing.unit}`}
        </td>
        <td className="whitespace-nowrap text-xs">
          {ing.next_reviewed ? (
            <span className={ing.overdue ? "text-error font-semibold" : "opacity-60"}>
              {ing.overdue ? "overdue " : "due "}
              {formatDate(ing.next_reviewed)}
            </span>
          ) : (
            <span className="opacity-50">never reviewed</span>
          )}
        </td>
        <td className="text-right whitespace-nowrap">
          {editing ? (
            <span className="inline-flex gap-1">
              <button type="button" className="btn btn-secondary btn-xs" onClick={onSave}>
                Save
              </button>
              <button type="button" className="btn btn-ghost btn-xs" onClick={onCancel}>
                Cancel
              </button>
            </span>
          ) : (
            <span className="inline-flex gap-1">
              <button type="button" className="btn btn-ghost btn-xs" onClick={onEdit}>
                Edit
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-xs"
                onClick={onReview}
                title="Confirm today's figures and advance the next review"
              >
                Reviewed
              </button>
              <button
                type="button"
                className={`btn btn-xs ${historyOpen ? "btn-secondary" : "btn-ghost"}`}
                onClick={onHistory}
                aria-label={`Price history for ${ing.name}`}
              >
                <History size={14} />
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-xs text-error"
                onClick={onDelete}
              >
                <Trash2 size={14} />
              </button>
            </span>
          )}
        </td>
      </tr>
      {editing && (
        <tr className="bg-base-200/40">
          <td colSpan={5}>
            <div className="grid grid-cols-2 gap-3 p-2 sm:grid-cols-4 lg:grid-cols-6">
              <Field label="Name">
                <input className={inputCls} value={editDraft.name} onChange={set("name")} />
              </Field>
              <Field label="Category">
                <select className={selectCls} value={editDraft.category_id} onChange={set("category_id")}>
                  <option value="">—</option>
                  {categories.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Unit">
                <select className={selectCls} value={editDraft.unit} onChange={set("unit")}>
                  {UNIT_OPTIONS.map((u) => (
                    <option key={u} value={u}>
                      {u}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Purchase qty">
                <input type="number" step="any" min="0.001" className={inputCls} value={editDraft.purchase_qty} onChange={set("purchase_qty")} />
              </Field>
              <Field label="Purchase price">
                <input type="number" step="any" min="0" className={inputCls} value={editDraft.purchase_price} onChange={set("purchase_price")} />
              </Field>
              <Field label="Currency">
                <select className={selectCls} value={editDraft.currency} onChange={set("currency")}>
                  {CURRENCIES.map((c) => (
                    <option key={c} value={c}>{c.toUpperCase()}</option>
                  ))}
                </select>
              </Field>
              <Field label="Density g/ml">
                <input type="number" step="any" min="0" className={inputCls} placeholder="mass ↔ volume only" value={editDraft.density_g_per_ml} onChange={set("density_g_per_ml")} />
              </Field>
              <Field label="Confidence">
                <select className={selectCls} value={editDraft.confidence} onChange={set("confidence")}>
                  {CONFIDENCE.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Invoice ref">
                <input className={inputCls} maxLength={255} value={editDraft.invoice_ref} onChange={set("invoice_ref")} />
              </Field>
              <Field label="Review interval (days)">
                <input type="number" min="1" max="3650" className={inputCls} value={editDraft.review_interval_days} onChange={set("review_interval_days")} />
              </Field>
              <div className="col-span-2">
                <Field label="Source">
                  <input className={inputCls} maxLength={1000} value={editDraft.source} onChange={set("source")} />
                </Field>
              </div>
            </div>
          </td>
        </tr>
      )}
      {historyOpen && (
        <tr className="bg-base-200/40">
          <td colSpan={5}>
            {history === null ? (
              <p className="p-2 text-xs opacity-60">Loading price history…</p>
            ) : history.length === 0 ? (
              <p className="p-2 text-xs opacity-60">No price changes recorded yet.</p>
            ) : (
              <div className="overflow-x-auto p-2">
                <table className="table table-xs">
                  <thead>
                    <tr className="uppercase opacity-50">
                      <th>From</th>
                      <th>By</th>
                      <th>Qty</th>
                      <th>Price</th>
                      <th>Per unit</th>
                      <th>Est.</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((ev, i) => (
                      <tr key={i}>
                        <td className="whitespace-nowrap">{formatDate(ev.effective_from)}</td>
                        <td>{ev.admin_name}</td>
                        <td className="whitespace-nowrap">
                          {ev.prev_purchase_qty ?? "—"} {ev.prev_unit} → {ev.new_purchase_qty ?? "—"} {ev.new_unit}
                        </td>
                        <td className="whitespace-nowrap">
                          {ev.prev_purchase_price ?? "—"} → {ev.new_purchase_price ?? "—"}
                        </td>
                        <td className="whitespace-nowrap">
                          {ev.prev_effective_cost_per_unit ?? "—"} → {ev.new_effective_cost_per_unit ?? "—"}
                        </td>
                        <td>{ev.new_is_estimate ? "yes" : "confirmed"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Recipes — one product's formula and its computed cost               */
/* ------------------------------------------------------------------ */

function RecipesTab({ products, selectedId, onSelect }) {
  const toast = useToast();
  const [ingredients, setIngredients] = useState(null);
  const [recipe, setRecipe] = useState(null);
  const [detail, setDetail] = useState(null); // product detail carrying cost
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    // The line picker wants every ingredient, whatever the page size.
    const all = [];
    const fetch = async (page) => {
      const { data } = await client.get("/admin/costing/ingredients", {
        params: { page, per_page: 100 },
      });
      all.push(...(data.ingredients ?? []));
      if (page < (data.pagination?.total_pages ?? 1) && page < 10) await fetch(page + 1);
    };
    fetch(1)
      .then(() => setIngredients(all))
      .catch((e) => toast.error(apiError(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadProduct = async (productId) => {
    setRecipe(null);
    setDetail(null);
    try {
      const [{ data: recipeData }, { data: detailData }] = await Promise.all([
        client.get(`/admin/costing/products/${productId}/recipe`).catch(() => ({ data: null })),
        client.get(`/admin/products/${productId}`),
      ]);
      setDetail(detailData);
      if (recipeData) {
        setRecipe({
          hands_on_minutes: String(recipeData.hands_on_minutes),
          unattended_minutes: String(recipeData.unattended_minutes),
          expected_batch_yield: String(recipeData.expected_batch_yield),
          batch_overhead_estimate: String(recipeData.batch_overhead_estimate),
          confidence: recipeData.confidence,
          notes: recipeData.notes ?? "",
          items: recipeData.items.map((it) => ({
            ingredient_id: it.ingredient_id,
            usable_qty: String(it.usable_qty),
            unit: it.unit,
            loss_pct: String(it.loss_pct ?? 0),
            note: it.note ?? "",
          })),
        });
      } else {
        setRecipe({ ...EMPTY_RECIPE, items: [] });
      }
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  useEffect(() => {
    if (selectedId) loadProduct(selectedId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  const setItem = (index, patch) =>
    setRecipe((r) => ({
      ...r,
      items: r.items.map((it, i) => (i === index ? { ...it, ...patch } : it)),
    }));

  const addItem = () =>
    setRecipe((r) => ({
      ...r,
      items: [...r.items, { ingredient_id: "", usable_qty: "", unit: "g", loss_pct: "0", note: "" }],
    }));

  const save = async () => {
    if (!recipe.expected_batch_yield || Number(recipe.expected_batch_yield) < 1) {
      toast.error("Expected batch yield must be at least 1");
      return;
    }
    for (const [i, item] of recipe.items.entries()) {
      if (!item.ingredient_id) {
        toast.error(`Line ${i + 1} needs an ingredient`);
        return;
      }
      if (!Number(item.usable_qty) || Number(item.usable_qty) <= 0) {
        toast.error(`Line ${i + 1} needs a usable quantity`);
        return;
      }
    }
    const ids = recipe.items.map((it) => Number(it.ingredient_id));
    if (new Set(ids).size !== ids.length) {
      toast.error("Each ingredient may appear once — edit its line instead");
      return;
    }
    setSaving(true);
    try {
      await client.put(`/admin/costing/products/${selectedId}/recipe`, {
        hands_on_minutes: Number(recipe.hands_on_minutes || 0),
        unattended_minutes: Number(recipe.unattended_minutes || 0),
        expected_batch_yield: Number(recipe.expected_batch_yield),
        batch_overhead_estimate: Number(recipe.batch_overhead_estimate || 0),
        confidence: recipe.confidence,
        notes: recipe.notes.trim() || null,
        items: recipe.items.map((it) => ({
          ingredient_id: Number(it.ingredient_id),
          usable_qty: Number(it.usable_qty),
          unit: it.unit,
          loss_pct: Number(it.loss_pct || 0),
          note: it.note.trim() || null,
        })),
      });
      toast.success("Recipe saved and cost recomputed");
      await loadProduct(selectedId);
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setSaving(false);
    }
  };

  if (products === null || ingredients === null) {
    return <Loading label="Opening the recipe book…" />;
  }

  const cost = detail?.cost ?? null;
  const costStatus = detail?.cost_status ?? "not_costed";
  const margins = cost?.margins ?? {};

  // The picker heads whatever card is showing, so switching products never
  // redraws the layout around it — only the body underneath changes.
  const picker = (
    <Field label="Product">
      <select
        className={`${selectCls} max-w-md`}
        value={selectedId ?? ""}
        onChange={(e) => onSelect(Number(e.target.value))}
      >
        <option value="">Choose a product…</option>
        {products.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </Field>
  );

  if (!selectedId || recipe === null) {
    return (
      <div className="space-y-6">
        <Card className="space-y-4">
          {picker}
          {selectedId ? (
            /* A form-shaped placeholder, so the card keeps its height while
               the recipe loads instead of collapsing to a one-liner. */
            <div className="space-y-3 py-2" aria-label="Loading the recipe">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
                {Array.from({ length: 6 }).map((_, i) => (
                  <div key={i} className="space-y-1.5">
                    <div className="skeleton h-3 w-16" />
                    <div className="skeleton h-8 w-full" />
                  </div>
                ))}
              </div>
              <div className="skeleton h-8 w-2/3" />
              <div className="skeleton h-8 w-1/2" />
            </div>
          ) : (
            <p className="pb-2 text-sm opacity-60">
              Pick a product to see or record its recipe.
            </p>
          )}
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
          <Card>
            {picker}
            <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              <Field label="Hands-on min">
                <input
                  type="number" min="0" step="any" className={inputCls}
                  value={recipe.hands_on_minutes}
                  onChange={(e) => setRecipe((r) => ({ ...r, hands_on_minutes: e.target.value }))}
                />
              </Field>
              <Field label="Unattended min">
                <input
                  type="number" min="0" step="any" className={inputCls}
                  value={recipe.unattended_minutes}
                  onChange={(e) => setRecipe((r) => ({ ...r, unattended_minutes: e.target.value }))}
                />
              </Field>
              <Field label="Expected yield *">
                <input
                  type="number" min="1" className={inputCls} placeholder="10"
                  value={recipe.expected_batch_yield}
                  onChange={(e) => setRecipe((r) => ({ ...r, expected_batch_yield: e.target.value }))}
                />
              </Field>
              <Field label="Batch overhead est.">
                <input
                  type="number" min="0" step="any" className={inputCls} placeholder="0"
                  value={recipe.batch_overhead_estimate}
                  onChange={(e) => setRecipe((r) => ({ ...r, batch_overhead_estimate: e.target.value }))}
                />
              </Field>
              <Field label="Confidence">
                <select
                  className={selectCls}
                  value={recipe.confidence}
                  onChange={(e) => setRecipe((r) => ({ ...r, confidence: e.target.value }))}
                >
                  {CONFIDENCE.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
              </Field>
              <div className="col-span-2 sm:col-span-1 lg:col-span-2">
                <Field label="Notes">
                  <input
                    className={inputCls} maxLength={4000}
                    value={recipe.notes}
                    onChange={(e) => setRecipe((r) => ({ ...r, notes: e.target.value }))}
                  />
                </Field>
              </div>
            </div>

            {/* Lines */}
            <div className="mt-4 overflow-x-auto">
              <table className="table table-sm">
                <thead>
                  <tr className="text-xs uppercase opacity-50">
                    <th>Ingredient</th>
                    <th>Usable qty</th>
                    <th>Unit</th>
                    <th>Loss %</th>
                    <th>Note</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {recipe.items.length === 0 && (
                    <tr>
                      <td colSpan={6} className="text-center text-sm opacity-50">
                        No lines yet — a recipe without lines records as incomplete.
                      </td>
                    </tr>
                  )}
                  {recipe.items.map((item, i) => {
                    const takenIds = new Set(
                      recipe.items.filter((_, j) => j !== i).map((it) => it.ingredient_id),
                    );
                    return (
                      <tr key={i}>
                        <td>
                          <select
                            className={selectCls}
                            value={item.ingredient_id}
                            onChange={(e) => setItem(i, { ingredient_id: e.target.value })}
                          >
                            <option value="">Choose…</option>
                            {ingredients.map((ing) => (
                              <option
                                key={ing.id}
                                value={ing.id}
                                disabled={takenIds.has(String(ing.id))}
                              >
                                {ing.name} ({ing.unit}, {money(ing.effective_cost_per_unit ?? 0, ing.currency)}/{ing.unit})
                              </option>
                            ))}
                          </select>
                        </td>
                        <td>
                          <input
                            type="number" min="0.001" step="any" className={`${inputCls} w-24`}
                            value={item.usable_qty}
                            onChange={(e) => setItem(i, { usable_qty: e.target.value })}
                          />
                        </td>
                        <td>
                          <select
                            className={`${selectCls} w-20`}
                            value={item.unit}
                            onChange={(e) => setItem(i, { unit: e.target.value })}
                          >
                            {UNIT_OPTIONS.map((u) => (
                              <option key={u} value={u}>{u}</option>
                            ))}
                          </select>
                        </td>
                        <td>
                          <input
                            type="number" min="0" max="99" step="any" className={`${inputCls} w-20`}
                            value={item.loss_pct}
                            onChange={(e) => setItem(i, { loss_pct: e.target.value })}
                          />
                        </td>
                        <td>
                          <input
                            className={inputCls} maxLength={255}
                            value={item.note}
                            onChange={(e) => setItem(i, { note: e.target.value })}
                          />
                        </td>
                        <td className="text-right">
                          <button
                            type="button"
                            className="btn btn-ghost btn-xs text-error"
                            onClick={() =>
                              setRecipe((r) => ({ ...r, items: r.items.filter((_, j) => j !== i) }))
                            }
                            aria-label={`Remove line ${i + 1}`}
                          >
                            <Trash2 size={14} />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-3">
              <button type="button" className="btn btn-ghost btn-sm" onClick={addItem}>
                <Plus size={16} /> Add line
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm min-w-28"
                onClick={save}
                disabled={saving}
              >
                {saving && <span className="loading loading-spinner loading-xs" />}
                Save recipe
              </button>
            </div>
          </Card>

          {/* Cost picture */}
          <Card>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="font-display text-lg font-semibold">Cost picture</h3>
              <StatusBadge status={costStatus} />
            </div>
            {!cost || cost.total_cost == null ? (
              <p className="mt-2 text-sm opacity-60">
                {cost && cost.missing?.length
                  ? `Missing: ${cost.missing.map(missingLabel).join(", ")}`
                  : "No complete cost yet — record the recipe, prices, overhead month and labour rate."}
              </p>
            ) : (
              <div className="mt-2 flex flex-wrap items-baseline gap-x-6 gap-y-1">
                <span className="font-display text-2xl font-bold">
                  {money(cost.total_cost, cost.currency)}
                  <span className="ml-1 text-sm font-normal opacity-60">per unit</span>
                </span>
                <span className="text-xs opacity-60">computed {formatDate(cost.computed_at)}</span>
                {["regular", "sale"].map((kind) => {
                  const m = margins[kind];
                  if (!m || m.status === "not_set") return null;
                  if (m.status === "not_computable") {
                    return (
                      <span key={kind} className="badge badge-sm badge-ghost border">
                        {kind}: not computable{m.reason?.code ? ` (${m.reason.code})` : ""}
                      </span>
                    );
                  }
                  return (
                    <span
                      key={kind}
                      className={`badge badge-sm border ${
                        m.status === "flagged" ? "badge-warning" : "badge-success"
                      }`}
                    >
                      {kind}: {(m.margin * 100).toFixed(1)}% margin · min net {money(m.min_net, cost.currency)}
                    </span>
                  );
                })}
                {cost.missing?.length > 0 && (
                  <span className="text-xs opacity-60">
                    still missing: {cost.missing.map(missingLabel).join(", ")}
                  </span>
                )}
              </div>
            )}
          </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Batches — production runs, closed with actuals                      */
/* ------------------------------------------------------------------ */

const EMPTY_CLOSE = { actual_yield: "", batch_overhead_actual: "", usage_notes: "" };

function BatchesTab({ products }) {
  const toast = useToast();
  const [batches, setBatches] = useState(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [form, setForm] = useState(EMPTY_BATCH);
  const [busy, setBusy] = useState(false);
  const [closingId, setClosingId] = useState(null);
  const [closeDraft, setCloseDraft] = useState(EMPTY_CLOSE);

  const [refreshing, setRefreshing] = useState(false);

  const load = () =>
    client
      .get("/admin/costing/batches", { params: { status: statusFilter || undefined } })
      .then(({ data }) => setBatches(data));

  useEffect(() => {
    // The old list stays up (dimmed) while the new filter loads.
    setRefreshing(true);
    load()
      .catch((e) => toast.error(apiError(e)))
      .finally(() => setRefreshing(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter]);

  const create = async (e) => {
    e.preventDefault();
    if (busy) return;
    if (!form.product_id) {
      toast.error("Choose the product being baked");
      return;
    }
    setBusy(true);
    try {
      await client.post("/admin/costing/batches", {
        product_id: Number(form.product_id),
        produced_at: form.produced_at || null,
        expected_yield: form.expected_yield ? Number(form.expected_yield) : null,
        batch_overhead_estimated: form.batch_overhead_estimated
          ? Number(form.batch_overhead_estimated)
          : null,
        overhead_categories: form.overhead_categories.trim() || null,
        usage_notes: form.usage_notes.trim() || null,
      });
      toast.success("Batch opened — yield and overhead default to the recipe");
      setForm(EMPTY_BATCH);
      await load();
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setBusy(false);
    }
  };

  const startClose = (batch) => {
    setClosingId(batch.id);
    setCloseDraft({
      actual_yield: "",
      batch_overhead_actual: String(batch.batch_overhead_estimated ?? ""),
      usage_notes: "",
    });
  };

  const confirmClose = async () => {
    const yield_ = Number(closeDraft.actual_yield);
    const overhead = Number(closeDraft.batch_overhead_actual);
    if (closeDraft.actual_yield === "" || yield_ < 0 || Number.isNaN(yield_)) {
      toast.error("Actual yield must be 0 or more");
      return;
    }
    if (closeDraft.batch_overhead_actual === "" || overhead < 0 || Number.isNaN(overhead)) {
      toast.error("Actual batch overhead must be 0 or more");
      return;
    }
    try {
      await client.post(`/admin/costing/batches/${closingId}/close`, {
        actual_yield: yield_,
        batch_overhead_actual: overhead,
        usage_notes: closeDraft.usage_notes.trim() || null,
      });
      toast.success("Batch closed — costs recomputed from the actuals");
      setClosingId(null);
      await load();
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  const productName = (id) => products?.find((p) => p.id === id)?.name ?? `Product ${id}`;

  return (
    <div className="space-y-6">
      <form
        onSubmit={create}
        className="grid gap-3 rounded-box glass p-4 shadow-sm ring-1 ring-base-300/60 sm:grid-cols-2 lg:grid-cols-[minmax(0,1.5fr)_150px_110px_130px_auto] lg:items-end"
      >
        <Field label="Product *">
          <select
            className={selectCls}
            required
            value={form.product_id}
            onChange={(e) => setForm((f) => ({ ...f, product_id: e.target.value }))}
          >
            <option value="">Choose…</option>
            {(products ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Produced on">
          <input
            type="date" className={inputCls}
            value={form.produced_at}
            onChange={(e) => setForm((f) => ({ ...f, produced_at: e.target.value }))}
          />
        </Field>
        <Field label="Yield">
          <input
            type="number" min="1" className={inputCls} placeholder="recipe"
            value={form.expected_yield}
            onChange={(e) => setForm((f) => ({ ...f, expected_yield: e.target.value }))}
          />
        </Field>
        <Field label="Overhead est.">
          <input
            type="number" min="0" step="any" className={inputCls} placeholder="recipe"
            value={form.batch_overhead_estimated}
            onChange={(e) => setForm((f) => ({ ...f, batch_overhead_estimated: e.target.value }))}
          />
        </Field>
        <button type="submit" className="btn btn-primary btn-sm min-w-28" disabled={busy}>
          {busy && <span className="loading loading-spinner loading-xs" />}
          Open batch
        </button>
        <div className="sm:col-span-2 lg:col-span-5">
          <Field label="Overhead categories (one per line — the double-count watchlist)">
            <textarea
              className="textarea textarea-bordered textarea-sm w-full" rows={1} maxLength={2000}
              placeholder="electricity&#10;rent share"
              value={form.overhead_categories}
              onChange={(e) => setForm((f) => ({ ...f, overhead_categories: e.target.value }))}
            />
          </Field>
        </div>
      </form>

      <div className="flex items-center gap-2">
        {["", "open", "closed"].map((s) => (
          <button
            key={s || "all"}
            type="button"
            onClick={() => setStatusFilter(s)}
            className={`btn btn-xs rounded-full font-semibold ${
              statusFilter === s ? "btn-secondary" : "btn-ghost border border-base-300"
            }`}
          >
            {s === "" ? "All" : s}
          </button>
        ))}
      </div>

      {batches === null ? (
        <Loading label="Counting the ovens…" />
      ) : (
        <div className={refreshing ? "opacity-60 transition-opacity" : "transition-opacity"}>
          {batches.length === 0 ? (
            <p className="rounded-box glass p-8 text-center text-sm opacity-60 ring-1 ring-base-300/60">
              No batches yet — open one when production starts.
            </p>
          ) : (
            <div className="overflow-x-auto rounded-box glass shadow-sm ring-1 ring-base-300/60">
          <table className="table table-sm">
            <thead>
              <tr className="text-xs uppercase opacity-50">
                <th>Product</th>
                <th>Produced</th>
                <th className="text-right">Yield exp → act</th>
                <th className="text-right">Variance</th>
                <th className="text-right">Overhead est → act</th>
                <th>Status</th>
                <th className="text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {batches.map((b) => (
                <tr key={b.id} className={b.status === "open" ? "" : "opacity-70"}>
                  <td className="font-semibold">{productName(b.product_id)}</td>
                  <td className="whitespace-nowrap text-xs opacity-70">{formatDate(b.produced_at)}</td>
                  <td className="text-right whitespace-nowrap">
                    {b.expected_yield} {b.actual_yield != null && `→ ${b.actual_yield}`}
                  </td>
                  <td className="text-right whitespace-nowrap">
                    {b.yield_variance_pct == null
                      ? "—"
                      : `${b.yield_variance_pct > 0 ? "+" : ""}${b.yield_variance_pct}%`}
                  </td>
                  <td className="text-right whitespace-nowrap">
                    {b.batch_overhead_estimated}
                    {b.batch_overhead_actual != null && ` → ${b.batch_overhead_actual}`}
                  </td>
                  <td>
                    <span
                      className={`badge badge-sm border ${
                        b.status === "open" ? "badge-info" : "badge-ghost"
                      }`}
                    >
                      {b.status}
                    </span>
                  </td>
                  <td className="text-right">
                    {b.status === "open" ? (
                      <button
                        type="button"
                        className="btn btn-ghost btn-xs"
                        onClick={() => startClose(b)}
                      >
                        Close batch
                      </button>
                    ) : (
                      <span className="text-xs opacity-50">
                        closed {formatDate(b.closed_at)}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
            </div>
          )}
        </div>
      )}

      {closingId && (
        <div className="modal modal-open">
          <div className="modal-box max-w-lg">
            <h3 className="font-display text-lg font-bold">Close this batch</h3>
            <p className="mt-1 text-sm opacity-70">
              Record what actually came out of the oven. Costing recomputes from
              the latest closed batch.
            </p>
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
              <Field label="Actual yield *">
                <input
                  type="number" min="0" className={inputCls} autoFocus
                  value={closeDraft.actual_yield}
                  onChange={(e) => setCloseDraft((d) => ({ ...d, actual_yield: e.target.value }))}
                />
              </Field>
              <Field label="Actual overhead *">
                <input
                  type="number" min="0" step="any" className={inputCls}
                  value={closeDraft.batch_overhead_actual}
                  onChange={(e) => setCloseDraft((d) => ({ ...d, batch_overhead_actual: e.target.value }))}
                />
              </Field>
              <Field label="Usage notes">
                <input
                  className={inputCls} maxLength={4000}
                  value={closeDraft.usage_notes}
                  onChange={(e) => setCloseDraft((d) => ({ ...d, usage_notes: e.target.value }))}
                />
              </Field>
            </div>
            <div className="modal-action">
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setClosingId(null)}
              >
                Keep it open
              </button>
              <button type="button" className="btn btn-error btn-sm" onClick={confirmClose}>
                Close batch
              </button>
            </div>
          </div>
          <div className="modal-backdrop" onClick={() => setClosingId(null)} />
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Overhead & labour — the shop-wide figures                           */
/* ------------------------------------------------------------------ */

function OverheadTab() {
  const toast = useToast();
  const [months, setMonths] = useState(null);
  const [rate, setRate] = useState(null);
  const [monthForm, setMonthForm] = useState(EMPTY_MONTH);
  const [rateForm, setRateForm] = useState(EMPTY_RATE);
  const [busyMonth, setBusyMonth] = useState(false);
  const [busyRate, setBusyRate] = useState(false);
  const [editingMonthId, setEditingMonthId] = useState(null);
  const [monthEdit, setMonthEdit] = useState({});

  const loadMonths = () =>
    client.get("/admin/costing/overhead-months").then(({ data }) => setMonths(data));
  const loadRate = () =>
    client.get("/admin/costing/labour-rate").then(({ data }) => setRate(data));

  useEffect(() => {
    loadMonths().catch((e) => toast.error(apiError(e)));
    loadRate().catch((e) => toast.error(apiError(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const warnOverlaps = (warnings) => {
    if (!warnings?.length) return;
    toast.info(
      `Possible double-count: ${warnings
        .map(
          (w) =>
            `${w.overlapping_categories.join(", ")} — also on batch #${w.batch_id}`,
        )
        .join("; ")}`,
    );
  };

  const saveMonth = async (e) => {
    e.preventDefault();
    if (busyMonth) return;
    if (!monthForm.month) {
      toast.error("Choose the month");
      return;
    }
    setBusyMonth(true);
    try {
      const { data } = await client.post("/admin/costing/overhead-months", {
        month: `${monthForm.month}-01`,
        fixed_overhead_total: Number(monthForm.fixed_overhead_total),
        forecast_hours: Number(monthForm.forecast_hours),
        currency: monthForm.currency.trim().toLowerCase(),
        included_categories: monthForm.included_categories.trim() || null,
        source: monthForm.source.trim() || null,
        confidence: monthForm.confidence,
      });
      toast.success(`Overhead for ${data.month.slice(0, 7)} saved — every costed product recomputed`);
      warnOverlaps(data.overlap_warnings);
      setMonthForm(EMPTY_MONTH);
      await loadMonths();
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setBusyMonth(false);
    }
  };

  const startMonthEdit = (m) => {
    setEditingMonthId(m.id);
    setMonthEdit({
      fixed_overhead_total: String(m.fixed_overhead_total),
      forecast_hours: String(m.forecast_hours),
      included_categories: m.included_categories ?? "",
      actual_total: m.actual_total == null ? "" : String(m.actual_total),
      actual_hours: m.actual_hours == null ? "" : String(m.actual_hours),
      variance_note: m.variance_note ?? "",
      confidence: m.confidence,
    });
  };

  const saveMonthEdit = async (m) => {
    try {
      const { data } = await client.put(`/admin/costing/overhead-months/${m.id}`, {
        fixed_overhead_total: Number(monthEdit.fixed_overhead_total),
        forecast_hours: Number(monthEdit.forecast_hours),
        included_categories: monthEdit.included_categories.trim() || null,
        actual_total: monthEdit.actual_total === "" ? null : Number(monthEdit.actual_total),
        actual_hours: monthEdit.actual_hours === "" ? null : Number(monthEdit.actual_hours),
        variance_note: monthEdit.variance_note.trim() || null,
        confidence: monthEdit.confidence,
      });
      toast.success(`Overhead for ${data.month.slice(0, 7)} updated`);
      warnOverlaps(data.overlap_warnings);
      setEditingMonthId(null);
      await loadMonths();
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  const saveRate = async (e) => {
    e.preventDefault();
    if (busyRate) return;
    if (!rateForm.valid_from || !Number(rateForm.rate)) {
      toast.error("A valid date and a rate above zero are required");
      return;
    }
    setBusyRate(true);
    try {
      const { data } = await client.put("/admin/costing/labour-rate", {
        valid_from: rateForm.valid_from,
        rate: Number(rateForm.rate),
        currency: rateForm.currency.trim().toLowerCase(),
        source: rateForm.source.trim() || null,
      });
      toast.success(
        data.source === "table"
          ? `Labour rate set — ${money(data.rate, data.currency)}/h from ${formatDate(data.valid_from)}`
          : `Labour rate saved — ${money(data.rate, data.currency)}/h`,
      );
      setRateForm(EMPTY_RATE);
      await loadRate();
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setBusyRate(false);
    }
  };

  return (
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
      {/* Overhead months */}
      <div className="space-y-6">
        <form
          onSubmit={saveMonth}
          className="grid grid-cols-2 gap-3 rounded-box glass p-4 shadow-sm ring-1 ring-base-300/60 sm:grid-cols-3 xl:grid-cols-5 xl:items-end"
        >
          <Field label="Month *">
            <input
              type="month" className={inputCls} required
              value={monthForm.month}
              onChange={(e) => setMonthForm((f) => ({ ...f, month: e.target.value }))}
            />
          </Field>
          <Field label="Fixed total *">
            <input
              type="number" min="0" step="any" className={inputCls} required placeholder="800"
              value={monthForm.fixed_overhead_total}
              onChange={(e) => setMonthForm((f) => ({ ...f, fixed_overhead_total: e.target.value }))}
            />
          </Field>
          <Field label="Forecast hours *">
            <input
              type="number" min="0.1" step="any" className={inputCls} required placeholder="160"
              value={monthForm.forecast_hours}
              onChange={(e) => setMonthForm((f) => ({ ...f, forecast_hours: e.target.value }))}
            />
          </Field>
          <Field label="Currency">
            <select
              className={selectCls}
              value={monthForm.currency}
              onChange={(e) => setMonthForm((f) => ({ ...f, currency: e.target.value }))}
            >
              {CURRENCIES.map((c) => (
                <option key={c} value={c}>{c.toUpperCase()}</option>
              ))}
            </select>
          </Field>
          <button type="submit" className="btn btn-primary btn-sm min-w-28" disabled={busyMonth}>
            {busyMonth && <span className="loading loading-spinner loading-xs" />}
            Save month
          </button>
          <div className="col-span-2 sm:col-span-3 xl:col-span-5">
            <Field label="Included categories (one per line)">
              <textarea
                className="textarea textarea-bordered textarea-sm w-full" rows={1} maxLength={2000}
                placeholder="rent share&#10;insurance"
                value={monthForm.included_categories}
                onChange={(e) => setMonthForm((f) => ({ ...f, included_categories: e.target.value }))}
              />
            </Field>
          </div>
        </form>

        {months === null ? (
          <Loading label="Opening the ledger…" />
        ) : months.length === 0 ? (
          <p className="rounded-box glass p-8 text-center text-sm opacity-60 ring-1 ring-base-300/60">
            No overhead months yet — costing needs one before it can allocate.
          </p>
        ) : (
          <div className="space-y-2">
            {months.map((m) => {
              const ratePerHour = overheadRatePerHour(m.fixed_overhead_total, m.forecast_hours);
              return (
                <Card key={m.id}>
                  {editingMonthId === m.id ? (
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                      <Field label="Fixed total">
                        <input
                          type="number" min="0" step="any" className={inputCls}
                          value={monthEdit.fixed_overhead_total}
                          onChange={(e) => setMonthEdit((d) => ({ ...d, fixed_overhead_total: e.target.value }))}
                        />
                      </Field>
                      <Field label="Forecast hours">
                        <input
                          type="number" min="0.1" step="any" className={inputCls}
                          value={monthEdit.forecast_hours}
                          onChange={(e) => setMonthEdit((d) => ({ ...d, forecast_hours: e.target.value }))}
                        />
                      </Field>
                      <Field label="Actual total">
                        <input
                          type="number" min="0" step="any" className={inputCls}
                          value={monthEdit.actual_total}
                          onChange={(e) => setMonthEdit((d) => ({ ...d, actual_total: e.target.value }))}
                        />
                      </Field>
                      <Field label="Actual hours">
                        <input
                          type="number" min="0" step="any" className={inputCls}
                          value={monthEdit.actual_hours}
                          onChange={(e) => setMonthEdit((d) => ({ ...d, actual_hours: e.target.value }))}
                        />
                      </Field>
                      <Field label="Confidence">
                        <select
                          className={selectCls}
                          value={monthEdit.confidence}
                          onChange={(e) => setMonthEdit((d) => ({ ...d, confidence: e.target.value }))}
                        >
                          {CONFIDENCE.map((c) => (
                            <option key={c} value={c}>{c}</option>
                          ))}
                        </select>
                      </Field>
                      <div className="col-span-2 sm:col-span-1">
                        <Field label="Variance note">
                          <input
                            className={inputCls} maxLength={2000}
                            value={monthEdit.variance_note}
                            onChange={(e) => setMonthEdit((d) => ({ ...d, variance_note: e.target.value }))}
                          />
                        </Field>
                      </div>
                      <div className="col-span-2 flex gap-2 sm:col-span-4">
                        <button type="button" className="btn btn-secondary btn-sm" onClick={() => saveMonthEdit(m)}>
                          Save
                        </button>
                        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditingMonthId(null)}>
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
                      <div>
                        <p className="font-display font-semibold">
                          {m.month.slice(0, 7)}
                          <EstBadge isEstimate={m.is_estimate} />
                        </p>
                        <p className="text-xs opacity-60">
                          {money(m.fixed_overhead_total, m.currency)} over {m.forecast_hours} h
                          {ratePerHour != null && ` · ${money(ratePerHour, m.currency)}/h`}
                          {m.actual_total != null && ` · actual ${money(m.actual_total, m.currency)} / ${m.actual_hours ?? "—"} h`}
                        </p>
                      </div>
                      <button type="button" className="btn btn-ghost btn-xs" onClick={() => startMonthEdit(m)}>
                        Edit
                      </button>
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
        )}
      </div>

      {/* Labour rate */}
      <div className="space-y-6">
        {rate === null ? (
          <Loading label="Checking the wage book…" />
        ) : (
          <Card>
            <h3 className="font-display text-lg font-semibold">Labour rate in force</h3>
            <p className="mt-1 font-display text-2xl font-bold">
              {money(rate.rate, rate.currency)}
              <span className="ml-1 text-sm font-normal opacity-60">per hour</span>
            </p>
            <p className="text-xs opacity-60">
              {rate.source === "table"
                ? `from the table, effective ${formatDate(rate.valid_from)}`
                : "the default fallback — record your real rate to replace it"}
            </p>
            {rate.history?.length > 0 && (
              <div className="mt-3 overflow-x-auto">
                <table className="table table-xs">
                  <thead>
                    <tr className="uppercase opacity-50">
                      <th>Effective from</th>
                      <th className="text-right">Rate</th>
                      <th>Source</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rate.history.map((r) => (
                      <tr key={r.valid_from}>
                        <td className="whitespace-nowrap">{formatDate(r.valid_from)}</td>
                        <td className="text-right">{money(r.rate, r.currency)}/h</td>
                        <td className="max-w-48 truncate text-xs opacity-60">{r.source ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        )}

        <form
          onSubmit={saveRate}
          className="grid grid-cols-2 gap-3 rounded-box glass p-4 shadow-sm ring-1 ring-base-300/60 sm:grid-cols-3 xl:grid-cols-5 xl:items-end"
        >
          <Field label="Effective from *">
            <input
              type="date" className={inputCls} required
              value={rateForm.valid_from}
              onChange={(e) => setRateForm((f) => ({ ...f, valid_from: e.target.value }))}
            />
          </Field>
          <Field label="Rate / hour *">
            <input
              type="number" min="0.01" step="any" className={inputCls} required placeholder="20.00"
              value={rateForm.rate}
              onChange={(e) => setRateForm((f) => ({ ...f, rate: e.target.value }))}
            />
          </Field>
          <Field label="Currency">
            <select
              className={selectCls}
              value={rateForm.currency}
              onChange={(e) => setRateForm((f) => ({ ...f, currency: e.target.value }))}
            >
              {CURRENCIES.map((c) => (
                <option key={c} value={c}>{c.toUpperCase()}</option>
              ))}
            </select>
          </Field>
          <Field label="Source">
            <input
              className={inputCls} maxLength={1000} placeholder="Minimum wage 2027"
              value={rateForm.source}
              onChange={(e) => setRateForm((f) => ({ ...f, source: e.target.value }))}
            />
          </Field>
          <button type="submit" className="btn btn-primary btn-sm min-w-28" disabled={busyRate}>
            {busyRate && <span className="loading loading-spinner loading-xs" />}
            Set rate
          </button>
        </form>
        <p className="text-xs opacity-50">
          Rates are append-only: a new effective date adds a row, history is never rewritten.
        </p>
      </div>
    </div>
  );
}
