import { useEffect, useState } from "react";
import { ImagePlus, Trash2 } from "lucide-react";
import client, { apiError } from "../../api/client";
import { money } from "../../lib/format";
import { shrinkImage } from "../../lib/imageResize";
import { useToast } from "../../context/ToastContext";
import ConfirmDialog from "../../components/ConfirmDialog";
import PhotoTile from "../../components/PhotoTile";
import { Loading } from "../../components/Loading";

const EMPTY_FORM = {
  name: "",
  description: "",
  image_url: "",
  price: "",
  sale_price: "",
  category_id: "",
  is_featured: false,
  is_available: true,
  stock: 0,
  prep_time_minutes: "",
  ingredients: "",
  allergens: "",
  // VAT classification — saved through its own audited endpoint
  vat_category: "",
  vat_review_status: "pending",
  milk_content_percent: "",
};

// The provisional categories the tax engine knows. Numbers stay out of the
// labels on purpose — the desk shows the implied rate from the server, and
// the label should never read like a legal determination.
const VAT_CATEGORIES = [
  ["FOOD_REDUCED", "Food — reduced rate"],
  ["BEVERAGE_STANDARD", "Beverage — standard rate"],
  ["MILK_DRINK", "Milk / milk-mixed drink — rate by milk content"],
  ["SMOOTHIE_REVIEW", "Smoothie — awaiting tax adviser"],
  ["SPECIAL_REVIEW", "Special case — awaiting tax adviser"],
];

const VAT_STATUSES = [
  ["pending", "Pending review"],
  ["approved", "Approved — internal sign-off"],
  ["blocked", "Blocked"],
];

// Categories that exist to park a product for the tax adviser — they never
// carry an approval, so picking one drops the status back to pending.
const REVIEW_PARKED = ["SMOOTHIE_REVIEW", "SPECIAL_REVIEW"];

const NUM_FIELDS = [
  ["price", "Price *", "0.01", "0.01"],
  ["sale_price", "Sale price — must be lower", "0.01", ""],
  ["stock", "Stock on hand *", "1", "0"],
  ["prep_time_minutes", "Prep time (minutes)", "1", "0"],
];

// Select sentinel — picking it swaps the dropdown for a "name a new shelf" box
const NEW_CATEGORY = "__new__";

export default function AdminProducts() {
  const toast = useToast();
  const [data, setData] = useState(null);
  const [categories, setCategories] = useState([]);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [shelfFilter, setShelfFilter] = useState(null); // category id, or the whole case
  const [editing, setEditing] = useState(null); // { id?, form, vat_rate_preview? }
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState(null);
  const [deletingShelf, setDeletingShelf] = useState(null);
  const [needsVatReview, setNeedsVatReview] = useState(false);

  const load = (p = page, s = search, cat = shelfFilter, vat = needsVatReview) => {
    const query = { page: p, per_page: 15 };
    if (s) query.search = s;
    if (cat) query.category_id = cat;
    if (vat) query.needs_vat_review = true;
    return client
      .get("/admin/products", { params: query })
      .then(({ data }) => setData(data));
  };

  useEffect(() => {
    client.get("/products/categories").then(({ data }) => setCategories(data));
  }, []);

  useEffect(() => {
    setData(null);
    load(page, search);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, search, shelfFilter, needsVatReview]);

  const openNew = () =>
    setEditing({
      id: null,
      form: { ...EMPTY_FORM },
      photo: null,
      newCategory: "",
      vat_rate_preview: null,
    });

  const openEdit = async (p) => {
    // Fetch the full record — the list view omits long-form fields.
    try {
      const { data } = await client.get(`/admin/products/${p.id}`);
      setEditing({
        id: p.id,
        form: {
          name: data.name,
          description: data.description ?? "",
          image_url: data.image_url ?? "",
          price: data.price,
          sale_price: data.sale_price ?? "",
          category_id:
            categories.find((c) => c.slug === data.category_slug)?.id ?? "",
          is_featured: data.is_featured,
          is_available: data.is_available,
          stock: data.stock,
          prep_time_minutes: data.prep_time_minutes ?? "",
          ingredients: data.ingredients ?? "",
          allergens: data.allergens ?? "",
          vat_category: data.vat_category ?? "",
          vat_review_status: data.vat_review_status ?? "pending",
          milk_content_percent: data.milk_content_percent ?? "",
        },
        photo: null,
        newCategory: "",
        vat_rate_preview: data.vat_rate_preview ?? null,
      });
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  const pickPhoto = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // lets the same file be picked again after a change of mind
    if (!file) return;
    try {
      const shrunk = await shrinkImage(file, 1200);
      setEditing((s) => ({
        ...s,
        photo: { file: shrunk, preview: URL.createObjectURL(shrunk) },
      }));
    } catch {
      toast.error(
        "That doesn't look like an image — JPEG, PNG or WebP, please.",
      );
    }
  };

  const clearPhoto = () =>
    setEditing((s) => {
      if (s.photo) URL.revokeObjectURL(s.photo.preview);
      return { ...s, photo: null, form: { ...s.form, image_url: "" } };
    });

  const closeDialog = () =>
    setEditing((s) => {
      if (s?.photo) URL.revokeObjectURL(s.photo.preview);
      return null;
    });

  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const f = editing.form;
      let image_url = f.image_url.trim() || null;
      if (editing.photo) {
        const form = new FormData();
        form.append("photo", editing.photo.file);
        const { data } = await client.post(
          "/admin/uploads/product-photo",
          form,
        );
        image_url = data.url;
      }

      // "New shelf…" — create it on the spot (or reuse one with that name)
      let categoryId = f.category_id;
      if (categoryId === NEW_CATEGORY) {
        const name = editing.newCategory.trim();
        const existing = categories.find(
          (c) => c.name.toLowerCase() === name.toLowerCase(),
        );
        if (existing) {
          categoryId = String(existing.id);
        } else if (name) {
          const { data } = await client.post("/admin/categories", { name });
          categoryId = String(data.id);
          setCategories((cs) => [...cs, data]);
        } else {
          toast.error("Name the new shelf first — or pick an existing one");
          return;
        }
      }

      const payload = {
        name: f.name,
        description: f.description || null,
        image_url,
        price: Number(f.price),
        sale_price: f.sale_price === "" ? null : Number(f.sale_price),
        category_id: categoryId ? Number(categoryId) : null,
        is_featured: f.is_featured,
        is_available: f.is_available,
        stock: Number(f.stock ?? 0),
        prep_time_minutes:
          f.prep_time_minutes === "" ? null : Number(f.prep_time_minutes),
        ingredients: f.ingredients || null,
        allergens: f.allergens || null,
      };
      // The classification travels on its own audited endpoint — an ordinary
      // product save can never wipe it, and every change is logged with who
      // and when.
      const vatPayload = {
        vat_category: f.vat_category || null,
        vat_review_status: f.vat_review_status,
        milk_content_percent:
          f.milk_content_percent === "" ? null : Number(f.milk_content_percent),
      };
      if (editing.id) {
        await client.put(`/admin/products/${editing.id}`, payload);
        await client.put(`/admin/products/${editing.id}/vat`, vatPayload);
        toast.success("Product updated");
      } else {
        const { data: created } = await client.post("/admin/products", payload);
        await client.put(`/admin/products/${created.id}/vat`, vatPayload);
        toast.success("Product added to the case");
      }
      closeDialog();
      await load();
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setBusy(false);
    }
  };

  const confirmDelete = async () => {
    try {
      await client.delete(`/admin/products/${deleting.id}`);
      toast.info(`Removed “${deleting.name}”`);
      setDeleting(null);
      await load();
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  const setField = (key, value) =>
    setEditing((s) => ({ ...s, form: { ...s.form, [key]: value } }));

  const removeShelf = async () => {
    const shelf = deletingShelf;
    setDeletingShelf(null);
    try {
      const { data } = await client.delete(`/admin/categories/${shelf.id}`);
      toast.info(data.message ?? `Removed “${shelf.name}” shelf`);
      const { data: cats } = await client.get("/products/categories");
      setCategories(cats);
      // dropping the shelf we're filtered on reloads via the effect below
      if (shelfFilter === shelf.id) setShelfFilter(null);
      else await load();
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <input
          className="input input-bordered input-sm w-56"
          placeholder="Search products…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
          aria-label="Search products"
        />
        <button
          type="button"
          className={`btn btn-sm ${needsVatReview ? "btn-warning" : "btn-ghost"}`}
          onClick={() => {
            setPage(1);
            setNeedsVatReview((v) => !v);
          }}
          aria-pressed={needsVatReview}
          title="Everything not yet signed off for VAT-inclusive pricing"
        >
          Needs VAT review
        </button>
        <div className="flex-1" />
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={openNew}
        >
          New product
        </button>
      </div>

      {/* Shelves — the case's layout at a glance. Tap one to bring its bakes
          up in the table below (tap it again, or clear, for the whole case);
          the trash retires any shelf — its bakes land unshelved, not gone. */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold tracking-wide uppercase opacity-50">
          Shelves
        </span>
        {categories.map((c) => {
          const active = shelfFilter === c.id;
          return (
            <div key={c.id} className="join rounded-full border border-base-300">
              <button
                type="button"
                className={`btn btn-xs join-item rounded-l-full border-0 ${
                  active
                    ? "btn-secondary text-secondary-content"
                    : "btn-ghost"
                }`}
                onClick={() => {
                  setPage(1);
                  setShelfFilter(active ? null : c.id);
                }}
                aria-pressed={active}
                title={
                  active
                    ? `Showing only ${c.name} — click for every bake`
                    : `Show only the ${c.name} shelf`
                }
              >
                {c.name}
                <span className="opacity-60">{c.product_count}</span>
              </button>
              <button
                type="button"
                className="btn btn-xs join-item btn-ghost rounded-r-full border-0 text-error"
                onClick={() => setDeletingShelf(c)}
                title={
                  c.product_count > 0
                    ? `Remove the shelf — its ${c.product_count} ${
                        c.product_count === 1 ? "bake stays" : "bakes stay"
                      }, unshelved`
                    : `Remove the ${c.name} shelf`
                }
                aria-label={`Remove the ${c.name} shelf`}
              >
                <Trash2 size={13} />
              </button>
            </div>
          );
        })}
        {shelfFilter && (
          <button
            type="button"
            className="btn btn-ghost btn-xs"
            onClick={() => {
              setPage(1);
              setShelfFilter(null);
            }}
          >
            × clear
          </button>
        )}
      </div>

      {data === null ? (
        <Loading label="Opening the case…" />
      ) : (
        <div className="overflow-x-auto rounded-box glass shadow-sm ring-1 ring-base-300/60">
          <table className="table table-sm">
            <thead>
              <tr className="text-xs uppercase opacity-50">
                <th>Product</th>
                <th>Category</th>
                <th className="text-right">Price</th>
                <th className="text-right">Stock</th>
                <th>Flags</th>
                <th className="text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.products.map((p) => (
                <tr key={p.id} className="hover:bg-base-200/50">
                  <td>
                    <div className="flex items-center gap-3">
                      <span className="h-9 w-9 shrink-0 overflow-hidden rounded-lg">
                        <PhotoTile
                          src={p.image_url}
                          name={p.name}
                          className="h-full w-full"
                        />
                      </span>
                      <span className="font-semibold">{p.name}</span>
                    </div>
                  </td>
                  <td className="text-sm opacity-70">
                    {p.category_name ?? "—"}
                  </td>
                  <td className="text-right">
                    {p.is_on_sale ? (
                      <span>
                        <span className="font-bold text-secondary">
                          {money(p.current_price)}
                        </span>{" "}
                        <span className="text-xs line-through opacity-50">
                          {money(p.price)}
                        </span>
                      </span>
                    ) : (
                      <span className="font-bold">
                        {money(p.current_price)}
                      </span>
                    )}
                  </td>
                  <td className="text-right">
                    <span
                      className={`badge badge-sm ${
                        p.stock === 0
                          ? "badge-error"
                          : p.stock <= 5
                            ? "badge-warning"
                            : "badge-ghost"
                      }`}
                    >
                      {p.stock}
                    </span>
                  </td>
                  <td>
                    <div className="flex gap-1">
                      {p.is_featured && (
                        <span className="badge badge-primary badge-xs">
                          favorite
                        </span>
                      )}
                      {!p.is_available && (
                        <span className="badge badge-neutral badge-xs">
                          hidden
                        </span>
                      )}
                      {p.vat_review_status === "approved" ? (
                        <span
                          className="badge badge-success badge-xs"
                          title="Internal sign-off recorded — still provisional until your tax adviser confirms"
                        >
                          VAT signed off
                        </span>
                      ) : (
                        <span
                          className={`badge badge-xs ${
                            p.vat_review_status === "blocked"
                              ? "badge-error"
                              : "badge-warning"
                          }`}
                          title="Not yet cleared for VAT-inclusive pricing"
                        >
                          {p.vat_review_status === "blocked"
                            ? "VAT blocked"
                            : p.vat_category
                              ? "VAT pending"
                              : "VAT unclassified"}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="text-right whitespace-nowrap">
                    <button
                      type="button"
                      className="btn btn-ghost btn-xs"
                      onClick={() => openEdit(p)}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="btn btn-ghost btn-xs text-error"
                      onClick={() => setDeleting({ id: p.id, name: p.name })}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
              {data.products.length === 0 && (
                <tr>
                  <td
                    colSpan={6}
                    className="py-10 text-center text-sm opacity-60"
                  >
                    Nothing found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {data && data.pagination.total_pages > 1 && (
        <div className="mt-4 flex justify-center">
          <div className="join">
            <button
              className="btn join-item btn-sm"
              disabled={page <= 1}
              onClick={() => setPage(page - 1)}
            >
              «
            </button>
            <button className="btn join-item btn-sm pointer-events-none">
              Page {data.pagination.page} / {data.pagination.total_pages}
            </button>
            <button
              className="btn join-item btn-sm"
              disabled={page >= data.pagination.total_pages}
              onClick={() => setPage(page + 1)}
            >
              »
            </button>
          </div>
        </div>
      )}

      {/* Create / edit dialog */}
      {editing && (
        <div className="modal modal-open">
          <div className="modal-box max-w-2xl">
            <h3 className="font-display text-xl font-bold">
              {editing.id ? "Edit product" : "New product"}
            </h3>
            <form
              onSubmit={save}
              id="product-form"
              className="mt-4 grid gap-3 sm:grid-cols-2"
            >
              <div className="sm:col-span-2">
                <label className="label text-xs">Name *</label>
                <input
                  className="input input-bordered w-full"
                  required
                  maxLength={150}
                  value={editing.form.name}
                  onChange={(e) => setField("name", e.target.value)}
                />
              </div>
              <div className="sm:col-span-2">
                <label className="label text-xs">Description</label>
                <textarea
                  className="textarea textarea-bordered w-full"
                  rows={2}
                  value={editing.form.description}
                  onChange={(e) => setField("description", e.target.value)}
                />
              </div>
              <div className="sm:col-span-2">
                <label className="label text-xs">Photo</label>
                <div className="flex items-center gap-4">
                  <span className="h-20 w-20 shrink-0 overflow-hidden rounded-xl ring-1 ring-base-300">
                    <PhotoTile
                      src={editing.photo?.preview ?? editing.form.image_url}
                      name={editing.form.name}
                      className="h-full w-full"
                    />
                  </span>
                  <div className="space-y-1.5">
                    <label className="btn btn-outline btn-sm">
                      <ImagePlus size={18} />
                      {editing.form.image_url || editing.photo
                        ? "Replace photo"
                        : "Upload photo"}
                      <input
                        type="file"
                        accept="image/*"
                        className="hidden"
                        onChange={pickPhoto}
                        aria-label="Product photo"
                      />
                    </label>
                    {(editing.form.image_url || editing.photo) && (
                      <button
                        type="button"
                        className="btn btn-ghost btn-xs text-error"
                        onClick={clearPhoto}
                      >
                        Remove photo
                      </button>
                    )}
                    <p className="text-xs opacity-60">
                      From this PC or straight off the phone camera. Left blank
                      shows a plain tile.
                    </p>
                  </div>
                </div>
              </div>
              <div>
                <label className="label text-xs">Category</label>
                <select
                  className="select select-bordered w-full"
                  value={editing.form.category_id}
                  onChange={(e) => setField("category_id", e.target.value)}
                >
                  <option value="">— none —</option>
                  {categories.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                  <option value={NEW_CATEGORY}>+ New shelf…</option>
                </select>
                {editing.form.category_id === NEW_CATEGORY && (
                  <div className="mt-2">
                    <label className="label text-xs">New shelf name</label>
                    <input
                      className="input input-bordered w-full"
                      placeholder="Shelf name — e.g. Tarts"
                      maxLength={100}
                      value={editing.newCategory}
                      onChange={(e) =>
                        setEditing((s) => ({ ...s, newCategory: e.target.value }))
                      }
                      aria-label="New category name"
                    />
                  </div>
                )}
              </div>
              {NUM_FIELDS.map(([key, label, step, min]) => (
                <div key={key}>
                  <label className="label text-xs">{label}</label>
                  <input
                    type="number"
                    className="input input-bordered w-full"
                    step={step}
                    min={min}
                    value={editing.form[key]}
                    onChange={(e) => setField(key, e.target.value)}
                  />
                </div>
              ))}
              <div className="sm:col-span-2">
                <label className="label text-xs">Ingredients</label>
                <input
                  className="input input-bordered w-full"
                  value={editing.form.ingredients}
                  onChange={(e) => setField("ingredients", e.target.value)}
                />
              </div>
              <div className="sm:col-span-2">
                <label className="label text-xs">Allergens</label>
                <input
                  className="input input-bordered w-full"
                  placeholder="Gluten, Dairy…"
                  value={editing.form.allergens}
                  onChange={(e) => setField("allergens", e.target.value)}
                />
              </div>

              {/* VAT classification — provisional until the tax adviser
                  confirms each product. Saved via its own audited endpoint. */}
              <div className="sm:col-span-2 rounded-box bg-base-200/60 p-4">
                <p className="mb-2 text-xs font-semibold tracking-wide uppercase opacity-60">
                  VAT classification
                </p>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <label className="label text-xs">Category</label>
                    <select
                      className="select select-bordered w-full"
                      value={editing.form.vat_category}
                      onChange={(e) => {
                        const category = e.target.value;
                        setEditing((s) => ({
                          ...s,
                          form: {
                            ...s.form,
                            vat_category: category,
                            // parking a product takes it off the approved
                            // list — those categories never carry approval
                            ...(REVIEW_PARKED.includes(category) &&
                            s.form.vat_review_status === "approved"
                              ? { vat_review_status: "pending" }
                              : {}),
                            // milk only belongs to milk drinks
                            ...(category !== "MILK_DRINK"
                              ? { milk_content_percent: "" }
                              : {}),
                          },
                        }));
                      }}
                    >
                      <option value="">— not classified yet —</option>
                      {VAT_CATEGORIES.map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="label text-xs">Review status</label>
                    <select
                      className="select select-bordered w-full"
                      value={editing.form.vat_review_status}
                      onChange={(e) =>
                        setField("vat_review_status", e.target.value)
                      }
                    >
                      {VAT_STATUSES.map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                    <p className="mt-1 text-[11px] leading-tight opacity-60">
                      Approving records your internal sign-off only — it does
                      not replace classification by your tax adviser.
                    </p>
                  </div>
                  {editing.form.vat_category === "MILK_DRINK" && (
                    <div>
                      <label className="label text-xs">
                        Cow's-milk content (%) *
                      </label>
                      <input
                        type="number"
                        className="input input-bordered w-full"
                        step="0.01"
                        min="0"
                        max="100"
                        value={editing.form.milk_content_percent}
                        onChange={(e) =>
                          setField("milk_content_percent", e.target.value)
                        }
                      />
                      <p className="mt-1 text-[11px] leading-tight opacity-60">
                        Cow's-milk share of the finished drink — 75% or more
                        earns the reduced rate. Plant-based milks never count;
                        enter their real cow's-milk share (usually 0).
                      </p>
                    </div>
                  )}
                  <div className="sm:col-span-2 space-y-2">
                    {editing.form.vat_review_status !== "approved" && (
                      <div
                        className="alert alert-warning py-2 text-xs"
                        role="status"
                      >
                        <span>
                          {editing.form.vat_category
                            ? "Provisional classification — not cleared for VAT-inclusive checkout yet. Await your tax adviser's confirmation before the euro cutover."
                            : "No VAT category yet — every product needs one (plus approval) before VAT-inclusive checkout can price it."}
                        </span>
                      </div>
                    )}
                    {editing.vat_rate_preview != null ? (
                      <p className="text-[11px] opacity-60">
                        Provisional rate implied by the saved classification:{" "}
                        {editing.vat_rate_preview}% — set by shop configuration,
                        not a legal determination.
                      </p>
                    ) : (
                      <p className="text-[11px] opacity-60">
                        The saved classification implies no rate yet.
                      </p>
                    )}
                  </div>
                </div>
              </div>
              <label className="label cursor-pointer justify-start gap-3">
                <input
                  type="checkbox"
                  className="toggle toggle-secondary toggle-sm"
                  checked={editing.form.is_featured}
                  onChange={(e) => setField("is_featured", e.target.checked)}
                />
                <span className="label-text text-sm">
                  Featured on the home page
                </span>
              </label>
              <label className="label cursor-pointer justify-start gap-3">
                <input
                  type="checkbox"
                  className="toggle toggle-secondary toggle-sm"
                  checked={editing.form.is_available}
                  onChange={(e) => setField("is_available", e.target.checked)}
                />
                <span className="label-text text-sm">Visible in the shop</span>
              </label>
            </form>
            <div className="modal-action">
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={closeDialog}
              >
                Cancel
              </button>
              <button
                type="submit"
                form="product-form"
                className="btn btn-primary btn-sm"
                disabled={busy}
              >
                {busy ? (
                  <span className="loading loading-spinner loading-xs" />
                ) : editing.id ? (
                  "Save changes"
                ) : (
                  "Add product"
                )}
              </button>
            </div>
          </div>
          <div className="modal-backdrop" onClick={closeDialog} />
        </div>
      )}

      {/* Shelf delete confirm */}
      {deletingShelf && (
        <ConfirmDialog
          title={`Remove the “${deletingShelf.name}” shelf?`}
          onConfirm={removeShelf}
          onCancel={() => setDeletingShelf(null)}
        >
          The shelf leaves the navbar, the shop sidebar and the home page.
          {deletingShelf.product_count > 0
            ? ` Its ${deletingShelf.product_count} ${
                deletingShelf.product_count === 1 ? "bake stays" : "bakes stay"
              } on the case, off this shelf — reshelve from each bake's edit form.`
            : " It holds no bakes, so nothing on the case changes."}
        </ConfirmDialog>
      )}

      {/* Delete confirm */}
      {deleting && (
        <ConfirmDialog
          title={`Remove “${deleting.name}”?`}
          onConfirm={confirmDelete}
          onCancel={() => setDeleting(null)}
        >
          It disappears from the shop immediately. Past orders keep their line
          items.
        </ConfirmDialog>
      )}
    </div>
  );
}
