import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, ImagePlus, Plus, Trash2 } from "lucide-react";
import client, { apiError } from "../../api/client";
import { money } from "../../lib/format";
import { shrinkImage } from "../../lib/imageResize";
import { useToast } from "../../context/ToastContext";
import ConfirmDialog from "../../components/ConfirmDialog";
import { Loading } from "../../components/Loading";

/** Shared plumbing for the two home-page managers below: patch one row,
 *  and swap a row with its neighbour by sort_order. */
function sortableResource(base, reload, toast) {
  const patch = async (item, body) => {
    try {
      await client.put(`${base}/${item.id}`, body);
      await reload();
    } catch (err) {
      toast.error(apiError(err));
    }
  };
  const move = async (items, item, dir) => {
    const idx = items.findIndex((i) => i.id === item.id);
    const swap = items[idx + dir];
    if (!swap) return;
    await patch(item, { sort_order: swap.sort_order });
    await patch(swap, { sort_order: item.sort_order });
  };
  return { patch, move };
}

/* ------------------------------------------------------------------ */
/* Coming soon — the home page's look-ahead band                       */
/* ------------------------------------------------------------------ */

const EMPTY_SOON = { title: "", description: "", image_url: "", eta_label: "", is_active: true };

function ComingSoonManager() {
  const toast = useToast();
  const [items, setItems] = useState(null);
  const [form, setForm] = useState(null); // null = closed, EMPTY_SOON = new, item = edit
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState(null);

  const load = () =>
    client.get("/admin/coming-soon").then(({ data }) => setItems(data));

  const { patch, move } = sortableResource("/admin/coming-soon", load, toast);

  useEffect(() => {
    load().catch(() => setItems([]));
  }, []);

  if (items === null) return <Loading label="Fetching the look-ahead…" />;

  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const body = {
        title: form.title,
        description: form.description || null,
        image_url: form.image_url || null,
        eta_label: form.eta_label || null,
        is_active: form.is_active,
        sort_order: form.sort_order ?? 99,
      };
      if (form.id) await client.put(`/admin/coming-soon/${form.id}`, body);
      else await client.post("/admin/coming-soon", body);
      setForm(null);
      await load();
      toast.success("Coming-soon band saved");
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setBusy(false);
    }
  };

  const pickImage = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true);
    try {
      const shrunk = await shrinkImage(file, 1200);
      const fd = new FormData();
      fd.append("photo", shrunk);
      const { data } = await client.post("/admin/uploads/hero-photo", fd);
      setForm((f) => ({ ...f, image_url: data.url }));
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    try {
      await client.delete(`/admin/coming-soon/${deleting.id}`);
      setDeleting(null);
      await load();
      toast.info("Removed");
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  return (
    <section className="rounded-box glass p-6 shadow-sm ring-1 ring-base-300/60">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-xl font-semibold">Coming soon</h2>
          <p className="mt-1 text-sm opacity-70">
            The home page&rsquo;s look-ahead band. Empty hides the section; shelved
            items keep their place here.
          </p>
        </div>
        {!form && (
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => setForm({ ...EMPTY_SOON })}
          >
            <Plus size={16} />
            Add an item
          </button>
        )}
      </div>

      {form && (
        <form onSubmit={save} className="mt-5 rounded-xl border border-base-300 bg-base-200/50 p-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <label className="label text-xs">Title</label>
              <input
                className="input input-bordered input-sm w-full"
                required
                maxLength={150}
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
              />
            </div>
            <div>
              <label className="label text-xs">When (short line, e.g. “From December”)</label>
              <input
                className="input input-bordered input-sm w-full"
                maxLength={100}
                value={form.eta_label}
                onChange={(e) => setForm({ ...form, eta_label: e.target.value })}
              />
            </div>
            <div>
              <label className="label text-xs">Photo</label>
              <div className="flex items-center gap-2">
                {form.image_url && (
                  <img
                    src={form.image_url}
                    alt=""
                    className="h-10 w-16 rounded-lg object-cover ring-1 ring-base-300"
                  />
                )}
                <label className="btn btn-outline btn-xs">
                  <ImagePlus size={14} />
                  Upload
                  <input type="file" accept="image/*" className="hidden" onChange={pickImage} disabled={busy} />
                </label>
                {form.image_url && (
                  <button
                    type="button"
                    className="btn btn-ghost btn-xs"
                    onClick={() => setForm({ ...form, image_url: "" })}
                  >
                    Clear
                  </button>
                )}
              </div>
            </div>
            <div className="sm:col-span-2">
              <label className="label text-xs">Description</label>
              <textarea
                className="textarea textarea-bordered w-full"
                rows={2}
                maxLength={1000}
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
            </div>
            <label className="label cursor-pointer justify-start gap-3 text-sm">
              <input
                type="checkbox"
                className="toggle toggle-secondary toggle-sm"
                checked={form.is_active}
                onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
              />
              Show on the home page
            </label>
          </div>
          <div className="mt-4 flex gap-2">
            <button type="submit" className="btn btn-primary btn-xs" disabled={busy}>
              {busy ? "Saving…" : form.id ? "Save item" : "Add item"}
            </button>
            <button type="button" className="btn btn-ghost btn-xs" onClick={() => setForm(null)}>
              Cancel
            </button>
          </div>
        </form>
      )}

      <ul className="mt-5 space-y-2">
        {items.map((item, idx) => (
          <li
            key={item.id}
            className={`flex flex-wrap items-center gap-3 rounded-xl border p-3 ${
              item.is_active ? "border-base-300 bg-base-200/40" : "border-dashed border-base-300 opacity-60"
            }`}
          >
            <span className="h-10 w-16 shrink-0 overflow-hidden rounded-lg bg-base-300">
              {item.image_url && <img src={item.image_url} alt="" className="h-full w-full object-cover" />}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold">
                {item.title}
                {item.eta_label && (
                  <span className="badge badge-secondary badge-xs ml-2 text-secondary-content">{item.eta_label}</span>
                )}
              </p>
              {item.description && (
                <p className="truncate text-xs opacity-60">{item.description}</p>
              )}
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                className="btn btn-ghost btn-xs btn-square"
                disabled={idx === 0}
                onClick={() => move(items, item, -1)}
                aria-label="Move up"
              >
                <ArrowUp size={14} />
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-xs btn-square"
                disabled={idx === items.length - 1}
                onClick={() => move(items, item, 1)}
                aria-label="Move down"
              >
                <ArrowDown size={14} />
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-xs"
                onClick={() => patch(item, { is_active: !item.is_active })}
              >
                {item.is_active ? "Shelve" : "Show"}
              </button>
              <button type="button" className="btn btn-ghost btn-xs" onClick={() => setForm(item)}>
                Edit
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-xs text-error"
                onClick={() => setDeleting(item)}
                aria-label={`Delete ${item.title}`}
              >
                <Trash2 size={14} />
              </button>
            </div>
          </li>
        ))}
        {items.length === 0 && (
          <li className="rounded-xl border border-dashed border-base-300 p-4 text-center text-sm opacity-60">
            Nothing on the horizon yet — add the first item.
          </li>
        )}
      </ul>

      {deleting && (
        <ConfirmDialog
          title={`Take “${deleting.title}” off the coming-soon band?`}
          onConfirm={remove}
          onCancel={() => setDeleting(null)}
        >
          It leaves the home page&rsquo;s look-ahead. You can add it back any
          time.
        </ConfirmDialog>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Testimonials — the home page's "Word around the neighborhood"       */
/* ------------------------------------------------------------------ */

const EMPTY_TESTIMONIAL = { name: "", role: "", quote: "", avatar_url: "", rating: "", is_active: true };

function TestimonialsManager() {
  const toast = useToast();
  const [items, setItems] = useState(null);
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState(null);

  const load = () =>
    client.get("/admin/testimonials").then(({ data }) => setItems(data));

  const { patch, move } = sortableResource("/admin/testimonials", load, toast);

  useEffect(() => {
    load().catch(() => setItems([]));
  }, []);

  if (items === null) return <Loading label="Fetching the kind words…" />;

  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const body = {
        name: form.name,
        role: form.role || null,
        quote: form.quote,
        avatar_url: form.avatar_url || null,
        rating: form.rating ? Number(form.rating) : null,
        is_active: form.is_active,
        sort_order: form.sort_order ?? 99,
      };
      if (form.id) await client.put(`/admin/testimonials/${form.id}`, body);
      else await client.post("/admin/testimonials", body);
      setForm(null);
      await load();
      toast.success("Testimonial saved");
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    try {
      await client.delete(`/admin/testimonials/${deleting.id}`);
      setDeleting(null);
      await load();
      toast.info("Deleted");
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  return (
    <section className="rounded-box glass p-6 shadow-sm ring-1 ring-base-300/60">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-xl font-semibold">Testimonials</h2>
          <p className="mt-1 text-sm opacity-70">
            The words on the home page. Copy a line from a product review, or
            add something a customer said at the counter.
          </p>
        </div>
        {!form && (
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => setForm({ ...EMPTY_TESTIMONIAL })}
          >
            <Plus size={16} />
            Add a testimonial
          </button>
        )}
      </div>

      {form && (
        <form onSubmit={save} className="mt-5 rounded-xl border border-base-300 bg-base-200/50 p-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="label text-xs">Name</label>
              <input
                className="input input-bordered input-sm w-full"
                required
                maxLength={150}
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </div>
            <div>
              <label className="label text-xs">Role / context (e.g. “Saturday regular”)</label>
              <input
                className="input input-bordered input-sm w-full"
                maxLength={100}
                value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value })}
              />
            </div>
            <div className="sm:col-span-2">
              <label className="label text-xs">Quote</label>
              <textarea
                className="textarea textarea-bordered w-full"
                rows={3}
                required
                minLength={5}
                maxLength={1000}
                value={form.quote}
                onChange={(e) => setForm({ ...form, quote: e.target.value })}
              />
            </div>
            <div>
              <label className="label text-xs">Stars (1–5, optional)</label>
              <input
                type="number"
                min="1"
                max="5"
                className="input input-bordered input-sm w-full"
                value={form.rating}
                onChange={(e) => setForm({ ...form, rating: e.target.value })}
              />
            </div>
            <div>
              <label className="label text-xs">Avatar URL (optional)</label>
              <input
                className="input input-bordered input-sm w-full"
                placeholder="/static/site/avatar-tanvir.jpg"
                maxLength={255}
                value={form.avatar_url}
                onChange={(e) => setForm({ ...form, avatar_url: e.target.value })}
              />
              <p className="mt-1 text-xs opacity-60">Empty shows the customer&rsquo;s initial instead.</p>
            </div>
            <label className="label cursor-pointer justify-start gap-3 text-sm">
              <input
                type="checkbox"
                className="toggle toggle-secondary toggle-sm"
                checked={form.is_active}
                onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
              />
              Show on the home page
            </label>
          </div>
          <div className="mt-4 flex gap-2">
            <button type="submit" className="btn btn-primary btn-xs" disabled={busy}>
              {busy ? "Saving…" : form.id ? "Save testimonial" : "Add testimonial"}
            </button>
            <button type="button" className="btn btn-ghost btn-xs" onClick={() => setForm(null)}>
              Cancel
            </button>
          </div>
        </form>
      )}

      <ul className="mt-5 space-y-2">
        {items.map((item, idx) => (
          <li
            key={item.id}
            className={`flex flex-wrap items-center gap-3 rounded-xl border p-3 ${
              item.is_active ? "border-base-300 bg-base-200/40" : "border-dashed border-base-300 opacity-60"
            }`}
          >
            {item.avatar_url ? (
              <img src={item.avatar_url} alt="" className="h-10 w-10 shrink-0 rounded-full object-cover ring-1 ring-base-300" />
            ) : (
              <span className="brand-gradient flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-sm font-bold text-secondary-content">
                {item.name.charAt(0).toUpperCase()}
              </span>
            )}
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold">
                {item.name}
                {item.role && <span className="ml-2 text-xs font-normal opacity-60">{item.role}</span>}
              </p>
              <p className="truncate text-xs opacity-60">
                {item.rating ? "★".repeat(item.rating) + " · " : ""}“{item.quote}”
              </p>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                className="btn btn-ghost btn-xs btn-square"
                disabled={idx === 0}
                onClick={() => move(items, item, -1)}
                aria-label="Move up"
              >
                <ArrowUp size={14} />
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-xs btn-square"
                disabled={idx === items.length - 1}
                onClick={() => move(items, item, 1)}
                aria-label="Move down"
              >
                <ArrowDown size={14} />
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-xs"
                onClick={() => patch(item, { is_active: !item.is_active })}
              >
                {item.is_active ? "Shelve" : "Show"}
              </button>
              <button type="button" className="btn btn-ghost btn-xs" onClick={() => setForm(item)}>
                Edit
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-xs text-error"
                onClick={() => setDeleting(item)}
                aria-label={`Delete testimonial from ${item.name}`}
              >
                <Trash2 size={14} />
              </button>
            </div>
          </li>
        ))}
        {items.length === 0 && (
          <li className="rounded-xl border border-dashed border-base-300 p-4 text-center text-sm opacity-60">
            No words yet — add the first one.
          </li>
        )}
      </ul>

      {deleting && (
        <ConfirmDialog
          title={`Delete the testimonial from ${deleting.name}?`}
          onConfirm={remove}
          onCancel={() => setDeleting(null)}
        >
          It comes off the home page. You can add it back any time.
        </ConfirmDialog>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* The Settings page itself                                            */
/* ------------------------------------------------------------------ */

/** Shop rules and storefront lines the baker sets once and forgets. */
export default function Settings() {
  const toast = useToast();
  const [saved, setSaved] = useState(null);
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState("");
  const [heroBusy, setHeroBusy] = useState(false);

  useEffect(() => {
    client.get("/admin/settings").then(({ data }) => {
      setSaved(data);
      setForm({
        fee: String(data.late_cancel_fee_percent),
        announcement: data.announcement ?? "",
        today_note: data.today_note ?? "",
        marquee: data.marquee_items ?? "",
        contact_address: data.contact_address ?? "",
        contact_phone: data.contact_phone ?? "",
        contact_email: data.contact_email ?? "",
        hours: data.opening_hours ?? "",
      });
    });
  }, []);

  if (!saved || !form) return <Loading label="Opening the rulebook…" />;

  const parsed = Number(form.fee);
  const feeValid =
    form.fee !== "" && Number.isFinite(parsed) && parsed >= 0 && parsed <= 100;
  const feeDirty = feeValid && parsed !== saved.late_cancel_fee_percent;
  const copyDirty =
    form.announcement !== (saved.announcement ?? "") ||
    form.today_note !== (saved.today_note ?? "") ||
    form.marquee !== (saved.marquee_items ?? "");
  const contactDirty =
    form.contact_address !== (saved.contact_address ?? "") ||
    form.contact_phone !== (saved.contact_phone ?? "") ||
    form.contact_email !== (saved.contact_email ?? "") ||
    form.hours !== (saved.opening_hours ?? "");
  const example = (40 * parsed) / 100; // a $40 box, for scale

  const save = async (part) => {
    setSaving(part);
    try {
      const body =
        part === "fee"
          ? { late_cancel_fee_percent: parsed }
          : part === "contact"
          ? {
              contact_address: form.contact_address,
              contact_phone: form.contact_phone,
              contact_email: form.contact_email,
              opening_hours: form.hours,
            }
          : {
              announcement: form.announcement,
              today_note: form.today_note,
              marquee_items: form.marquee,
            };
      const { data } = await client.put("/admin/settings", body);
      setSaved(data);
      setForm((f) => ({ ...f, fee: String(data.late_cancel_fee_percent) }));
      if (part === "fee") {
        toast.success(
          data.late_cancel_fee_percent > 0
            ? `Late cancels now keep ${data.late_cancel_fee_percent}% of the order`
            : "Late cancellations are free again",
        );
      } else if (part === "contact") {
        toast.success("Contact details saved");
      } else {
        toast.success("Storefront copy saved");
      }
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setSaving("");
    }
  };

  const pickHero = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // lets the same file be picked again after a change of mind
    if (!file) return;
    setHeroBusy(true);
    try {
      const shrunk = await shrinkImage(file, 1200);
      const form = new FormData();
      form.append("photo", shrunk);
      const { data: uploaded } = await client.post(
        "/admin/uploads/hero-photo",
        form,
      );
      const { data } = await client.put("/admin/settings", {
        hero_image: uploaded.url,
      });
      setSaved(data);
      toast.success("Home page photo updated");
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setHeroBusy(false);
    }
  };

  const resetHero = async () => {
    setHeroBusy(true);
    try {
      const { data } = await client.put("/admin/settings", { hero_image: "" });
      setSaved(data);
      toast.info("Back to the original counter photo");
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setHeroBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <section className="rounded-box glass p-6 shadow-sm ring-1 ring-base-300/60">
        <h2 className="font-display text-xl font-semibold">Home page photo</h2>
        <p className="mt-1 text-sm opacity-70">
          The picture above the “Today” note — swap it with the season or a
          fresh counter shot.
        </p>

        <div className="mt-5 flex flex-wrap items-center gap-5">
          <img
            src={saved.hero_image}
            alt="Current home page photo"
            className="aspect-[6/5] w-40 rounded-box object-cover ring-1 ring-base-300"
          />
          <div className="space-y-1.5">
            <label className="btn btn-outline btn-sm">
              <ImagePlus size={18} />
              {heroBusy ? "Uploading…" : "Upload photo"}
              <input
                type="file"
                accept="image/*"
                className="hidden"
                onChange={pickHero}
                aria-label="Home page photo"
                disabled={heroBusy}
              />
            </label>
            <button
              type="button"
              className="btn btn-ghost btn-xs"
              onClick={resetHero}
              disabled={heroBusy}
            >
              Use the original photo
            </button>
            <p className="text-xs opacity-60">
              JPEG, PNG or WebP. “The original” puts the shipped counter photo
              back.
            </p>
          </div>
        </div>
      </section>

      <section className="rounded-box glass p-6 shadow-sm ring-1 ring-base-300/60">
        <h2 className="font-display text-xl font-semibold">Storefront copy</h2>
        <p className="mt-1 text-sm opacity-70">
          The lines shoppers see first. Clear a box to hide it from the shop.
        </p>

        <div className="mt-5 grid gap-5 sm:grid-cols-2">
          <div>
            <label
              className="label mb-1.5 text-xs font-semibold"
              htmlFor="announcement"
            >
              Announcement bar
            </label>
            <textarea
              id="announcement"
              rows={2}
              maxLength={255}
              className="textarea textarea-bordered w-full resize-none"
              value={form.announcement}
              onChange={(e) =>
                setForm({ ...form, announcement: e.target.value })
              }
            />
          </div>
          <div>
            <label
              className="label mb-1.5 text-xs font-semibold"
              htmlFor="today-note"
            >
              “Today” note on the home page
            </label>
            <textarea
              id="today-note"
              rows={2}
              maxLength={255}
              className="textarea textarea-bordered w-full resize-none"
              value={form.today_note}
              onChange={(e) => setForm({ ...form, today_note: e.target.value })}
            />
          </div>
          <div className="sm:col-span-2">
            <label
              className="label mb-1.5 text-xs font-semibold"
              htmlFor="marquee"
            >
              Scrolling strip — one phrase per line
            </label>
            <textarea
              id="marquee"
              rows={4}
              maxLength={1000}
              className="textarea textarea-bordered w-full resize-none font-mono text-xs tracking-wide uppercase"
              value={form.marquee}
              onChange={(e) => setForm({ ...form, marquee: e.target.value })}
            />
            <p className="mt-1.5 text-xs opacity-60">
              The band that drifts across the home page. Empty lines are
              skipped; clear the box to hide the strip.
            </p>
          </div>
        </div>

        <div className="mt-5 flex items-center gap-3">
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => save("copy")}
            disabled={!copyDirty || saving !== ""}
          >
            {saving === "copy" ? "Saving…" : "Save copy"}
          </button>
          {copyDirty && (
            <span className="text-xs opacity-60">Unsaved change</span>
          )}
        </div>
      </section>

      <section className="rounded-box glass p-6 shadow-sm ring-1 ring-base-300/60">
        <h2 className="font-display text-xl font-semibold">Contact &amp; hours</h2>
        <p className="mt-1 text-sm opacity-70">
          The real lines for the footer — customers see exactly what you type
          here, and anything left empty simply doesn&rsquo;t show.
        </p>

        <div className="mt-5 grid gap-5 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <label className="label mb-1.5 text-xs font-semibold" htmlFor="contact-address">
              Street address
            </label>
            <input
              id="contact-address"
              className="input input-bordered w-full"
              maxLength={255}
              placeholder="12 Crumb Lane, Portland"
              value={form.contact_address}
              onChange={(e) => setForm({ ...form, contact_address: e.target.value })}
            />
          </div>
          <div>
            <label className="label mb-1.5 text-xs font-semibold" htmlFor="contact-phone">
              Phone
            </label>
            <input
              id="contact-phone"
              className="input input-bordered w-full"
              maxLength={50}
              placeholder="+1 (503) 555-0100"
              value={form.contact_phone}
              onChange={(e) => setForm({ ...form, contact_phone: e.target.value })}
            />
          </div>
          <div>
            <label className="label mb-1.5 text-xs font-semibold" htmlFor="contact-email">
              Contact email
            </label>
            <input
              id="contact-email"
              type="email"
              className="input input-bordered w-full"
              maxLength={255}
              placeholder="hello@yourbakery.com"
              value={form.contact_email}
              onChange={(e) => setForm({ ...form, contact_email: e.target.value })}
            />
          </div>
          <div className="sm:col-span-2">
            <label className="label mb-1.5 text-xs font-semibold" htmlFor="opening-hours">
              Bake hours — one line per row
            </label>
            <textarea
              id="opening-hours"
              rows={4}
              maxLength={500}
              className="textarea textarea-bordered w-full resize-none"
              placeholder={"Tue – Fri: 7:00 – 18:00\nSaturday: 7:00 – 15:00\nSun – Mon: closed"}
              value={form.hours}
              onChange={(e) => setForm({ ...form, hours: e.target.value })}
            />
            <p className="mt-1.5 text-xs opacity-60">
              Each line shows in the footer as you write it. Clear the box to
              hide the hours column.
            </p>
          </div>
        </div>

        <div className="mt-5 flex items-center gap-3">
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => save("contact")}
            disabled={!contactDirty || saving !== ""}
          >
            {saving === "contact" ? "Saving…" : "Save contact"}
          </button>
          {contactDirty && <span className="text-xs opacity-60">Unsaved change</span>}
        </div>
      </section>

      <ComingSoonManager />
      <TestimonialsManager />

      <section className="rounded-box glass p-6 shadow-sm ring-1 ring-base-300/60">
        <h2 className="font-display text-xl font-semibold">
          Cancellation policy
        </h2>
        <p className="mt-1 text-sm opacity-70">
          Cancelling is free until an order goes into the oven — this is the
          slice the shop keeps after that.
        </p>

        <div className="mt-5 max-w-xs">
          <label
            className="label mb-1.5 text-xs font-semibold"
            htmlFor="cancel-fee"
          >
            Late-cancellation fee (% of order)
          </label>
          <input
            id="cancel-fee"
            type="number"
            min="0"
            max="100"
            step="0.5"
            required
            className={
              feeValid
                ? "input input-bordered w-full"
                : "input input-bordered input-error w-full"
            }
            value={form.fee}
            onChange={(e) => setForm({ ...form, fee: e.target.value })}
          />
          <p className="mt-1.5 text-xs opacity-60">
            {feeValid && parsed > 0
              ? `A cancelled $40 box keeps ${money(example)} · set 0 to turn it off`
              : "Every cancellation is free, whenever it happens"}
          </p>
        </div>

        <div className="mt-5 flex items-center gap-3">
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => save("fee")}
            disabled={!feeDirty || saving !== ""}
          >
            {saving === "fee" ? "Saving…" : "Save policy"}
          </button>
          {feeDirty && (
            <span className="text-xs opacity-60">Unsaved change</span>
          )}
        </div>
      </section>
    </div>
  );
}
