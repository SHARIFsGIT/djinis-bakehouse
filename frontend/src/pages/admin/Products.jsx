import { useEffect, useState } from "react";
import { ImagePlus, Trash2 } from "lucide-react";
import client, { apiError } from "../../api/client";
import { money } from "../../lib/format";
import { shrinkImage } from "../../lib/imageResize";
import { useToast } from "../../context/ToastContext";
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
};

const NUM_FIELDS = [
  ["price", "Price ($) *", "0.01", "0.01"],
  ["sale_price", "Sale price ($) — must be lower", "0.01", ""],
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
  const [editing, setEditing] = useState(null); // { id?, form }
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState(null);
  const [deletingShelf, setDeletingShelf] = useState(null);

  const load = (p = page, s = search) => {
    const query = { page: p, per_page: 15 };
    if (s) query.search = s;
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
  }, [page, search]);

  const openNew = () =>
    setEditing({
      id: null,
      form: { ...EMPTY_FORM },
      photo: null,
      newCategory: "",
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
        },
        photo: null,
        newCategory: "",
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
      if (editing.id) {
        await client.put(`/admin/products/${editing.id}`, payload);
        toast.success("Product updated");
      } else {
        await client.post("/admin/products", payload);
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
    try {
      await client.delete(`/admin/categories/${deletingShelf.id}`);
      toast.info(`Removed “${deletingShelf.name}” shelf`);
      setDeletingShelf(null);
      const { data } = await client.get("/products/categories");
      setCategories(data);
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
        <div className="flex-1" />
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={openNew}
        >
          New product
        </button>
      </div>

      {/* Shelves — the case's layout at a glance. An empty shelf can be
          retired with one tap; an occupied one explains why not. */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold tracking-wide uppercase opacity-50">
          Shelves
        </span>
        {categories.map((c) => (
          <span
            key={c.id}
            className="badge badge-ghost gap-1.5 rounded-full py-2.5 pl-3 pr-2 text-sm"
          >
            {c.name}
            <span className="opacity-50">{c.product_count}</span>
            <button
              type="button"
              className="btn btn-ghost btn-xs btn-square text-error"
              onClick={() => setDeletingShelf(c)}
              disabled={c.product_count > 0}
              title={
                c.product_count > 0
                  ? `Move or remove its ${c.product_count} ${
                      c.product_count === 1 ? "bake" : "bakes"
                    } first`
                  : `Remove the ${c.name} shelf`
              }
              aria-label={`Remove the ${c.name} shelf`}
            >
              <Trash2 size={13} />
            </button>
          </span>
        ))}
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
        <div className="modal modal-open">
          <div className="modal-box max-w-sm">
            <h3 className="font-display text-lg font-bold">
              Remove the “{deletingShelf.name}” shelf?
            </h3>
            <p className="mt-1 text-sm opacity-70">
              The shelf leaves the navbar, the shop sidebar and the home page.
              It holds no bakes, so nothing on the case changes.
            </p>
            <div className="modal-action">
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setDeletingShelf(null)}
              >
                Keep it
              </button>
              <button
                type="button"
                className="btn btn-error btn-sm"
                onClick={removeShelf}
              >
                Yes, remove
              </button>
            </div>
          </div>
          <div className="modal-backdrop" onClick={() => setDeletingShelf(null)} />
        </div>
      )}

      {/* Delete confirm */}
      {deleting && (
        <div className="modal modal-open">
          <div className="modal-box max-w-sm">
            <h3 className="font-display text-lg font-bold">
              Remove “{deleting.name}”?
            </h3>
            <p className="mt-1 text-sm opacity-70">
              It disappears from the shop immediately. Past orders keep their
              line items.
            </p>
            <div className="modal-action">
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setDeleting(null)}
              >
                Keep it
              </button>
              <button
                type="button"
                className="btn btn-error btn-sm"
                onClick={confirmDelete}
              >
                Yes, remove
              </button>
            </div>
          </div>
          <div className="modal-backdrop" onClick={() => setDeleting(null)} />
        </div>
      )}
    </div>
  );
}
