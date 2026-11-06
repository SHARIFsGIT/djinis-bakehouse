import { Percent, Plus, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import client, { apiError } from "../../api/client";
import ConfirmDialog from "../../components/ConfirmDialog";
import Drawer from "../../components/Drawer";
import { useToast } from "../../context/ToastContext";
import { formatDate, money } from "../../lib/format";
import { REDEMPTION_RULES, oncePerCustomerFromRule, redemptionRule } from "../../lib/redemptionRules";

/* The promotions desk. One page, three stable zones — a summary strip, a
   filter row and the coupon table — and every write happens in the drawer
   that floats over them, so opening, closing or failing never moves the
   page underneath. Loading lives inside fixed-size buttons and reserved
   table space; nothing on this page is allowed to change shape. */

const EMPTY_FORM = { code: "", percent_off: "", min_subtotal: "", description: "", rule: "any" };
const STATUS_FILTERS = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "paused", label: "Paused" },
];

function StatChip({ label, value, tone = "" }) {
  return (
    <div className="glass flex min-h-17 items-center justify-between gap-3 rounded-box px-4 py-3 ring-1 ring-base-300/60">
      <span className="text-xs font-bold tracking-widest uppercase opacity-55">{label}</span>
      <span className={`font-display text-2xl font-bold ${tone}`}>{value}</span>
    </div>
  );
}

/* The floating editor. Create and edit share one form; the code is the
   coupon's public identity, so it is typed once at birth and read after. */
function CouponDrawer({ coupon, onClose, onSaved }) {
  const toast = useToast();
  const editing = coupon !== null;
  const firstField = useRef(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    setForm(
      editing
        ? {
            code: coupon.code,
            percent_off: String(coupon.percent_off),
            min_subtotal: coupon.min_subtotal == null ? "" : String(coupon.min_subtotal),
            description: coupon.description ?? "",
            rule: redemptionRule(coupon.once_per_customer),
          }
        : EMPTY_FORM,
    );
    setTouched(false);
    // The drawer opens onto the first field, keyboard and all.
    const timer = setTimeout(() => firstField.current?.focus(), 50);
    return () => clearTimeout(timer);
  }, [coupon, editing]);

  const percent = Number(form.percent_off);
  const minimum = form.min_subtotal.trim() === "" ? null : Number(form.min_subtotal);
  const percentError =
    touched && (!Number.isFinite(percent) || percent < 1 || percent > 100)
      ? "A whole percent, 1 to 100"
      : null;
  const minError =
    touched && (minimum !== null && (!Number.isFinite(minimum) || minimum < 0))
      ? "A euro amount of 0 or more"
      : null;

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const save = async (e) => {
    e.preventDefault();
    setTouched(true);
    if (percentError || minError || form.code.trim().length < 3) return;
    setBusy(true);
    try {
      if (editing) {
        await client.put(`/admin/coupons/${coupon.id}`, {
          percent_off: percent,
          description: form.description.trim() || null,
          min_subtotal: minimum,
          once_per_customer: oncePerCustomerFromRule(form.rule),
        });
        toast.success(`${coupon.code} updated`);
      } else {
        await client.post("/admin/coupons", {
          code: form.code.trim(),
          percent_off: percent,
          description: form.description.trim() || null,
          min_subtotal: minimum,
          once_per_customer: oncePerCustomerFromRule(form.rule),
        });
        toast.success(`${form.code.trim().toUpperCase()} is live`);
      }
      onSaved();
    } catch (err) {
      toast.error(apiError(err));
      setBusy(false);
    }
  };

  return (
    <Drawer
      title={editing ? `Edit ${coupon.code}` : "New promotion"}
      onClose={onClose}
      busy={busy}
    >
      <form onSubmit={save} className="flex flex-1 flex-col gap-4 overflow-y-auto px-6 py-5">
          <div>
            <label className="label text-xs" htmlFor="pr-code">Code</label>
            <input
              id="pr-code"
              ref={firstField}
              className={`input input-bordered w-full font-mono uppercase ${editing ? "opacity-60" : ""}`}
              required
              minLength={3}
              maxLength={50}
              pattern="[A-Za-z0-9]+"
              title="Letters and numbers only"
              placeholder="WELCOME10"
              value={form.code}
              onChange={set("code")}
              disabled={editing}
              readOnly={editing}
            />
            <p className="mt-1 text-xs opacity-60">
              {editing ? "The code is its public identity — it stays as typed at birth." : "Letters and numbers, typed once: this is what customers will enter."}
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label text-xs" htmlFor="pr-percent">Percent off</label>
              <input
                id="pr-percent"
                type="number"
                className={`input input-bordered w-full ${percentError ? "input-error" : ""}`}
                required
                min={1}
                max={100}
                step={1}
                placeholder="10"
                value={form.percent_off}
                onChange={set("percent_off")}
                aria-invalid={Boolean(percentError)}
              />
              {percentError && <p className="mt-1 text-xs text-error">{percentError}</p>}
            </div>
            <div>
              <label className="label text-xs" htmlFor="pr-min">Minimum spend (€)</label>
              <input
                id="pr-min"
                type="number"
                className={`input input-bordered w-full ${minError ? "input-error" : ""}`}
                min={0}
                step="0.01"
                placeholder="none"
                value={form.min_subtotal}
                onChange={set("min_subtotal")}
                aria-invalid={Boolean(minError)}
              />
              {minError && <p className="mt-1 text-xs text-error">{minError}</p>}
            </div>
          </div>

          <div>
            <label className="label text-xs" htmlFor="pr-rule">Customer limit</label>
            <select
              id="pr-rule"
              className="select select-bordered w-full"
              value={form.rule}
              onChange={set("rule")}
            >
              {REDEMPTION_RULES.map((rule) => (
                <option key={rule.value} value={rule.value}>{rule.label}</option>
              ))}
            </select>
            <p className="mt-1 text-xs opacity-60">
              A 1-per-customer code frees up again if that order is cancelled.
            </p>
          </div>

          <div>
            <label className="label text-xs" htmlFor="pr-note">Note</label>
            <input
              id="pr-note"
              className="input input-bordered w-full"
              maxLength={255}
              placeholder="First-order welcome discount"
              value={form.description}
              onChange={set("description")}
            />
          </div>

          {editing && coupon.min_subtotal != null && !coupon.min_subtotal_approved && (
            <p className="rounded-lg bg-warning/10 px-3 py-2 text-xs opacity-80">
              This minimum was recorded before the euro cutover — re-saving it here approves
              it as a euro threshold.
            </p>
          )}

          <div className="mt-auto flex justify-end gap-2 border-t border-base-300/70 pt-4">
            <button
              type="button"
              className="btn btn-ghost btn-sm min-w-24"
              onClick={onClose}
              disabled={busy}
            >
              Cancel
            </button>
            <button type="submit" className="btn btn-primary btn-sm min-w-28" disabled={busy}>
              {busy && <span className="loading loading-spinner loading-xs" />}
              {editing ? "Save changes" : "Create"}
            </button>
          </div>
      </form>
    </Drawer>
  );
}

export default function Promotions() {
  const toast = useToast();
  const [coupons, setCoupons] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [drawer, setDrawer] = useState(null); // null | {} (create) | coupon (edit)
  const [deleting, setDeleting] = useState(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [togglingId, setTogglingId] = useState(null);

  const load = () =>
    client
      .get("/admin/coupons")
      .then(({ data }) => setCoupons(data))
      .catch((e) => toast.error(apiError(e)))
      .finally(() => setLoaded(true));

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const visible = useMemo(() => {
    let list = coupons ?? [];
    if (status === "active") list = list.filter((c) => c.is_active);
    if (status === "paused") list = list.filter((c) => !c.is_active);
    const needle = search.trim().toLowerCase();
    if (needle) {
      list = list.filter(
        (c) =>
          c.code.toLowerCase().includes(needle) ||
          (c.description ?? "").toLowerCase().includes(needle),
      );
    }
    return list;
  }, [coupons, search, status]);

  const summary = useMemo(() => {
    const list = coupons ?? [];
    return {
      active: list.filter((c) => c.is_active).length,
      paused: list.filter((c) => !c.is_active).length,
      onceEach: list.filter((c) => c.once_per_customer).length,
      redemptions: list.reduce((sum, c) => sum + (c.redemptions ?? 0), 0),
    };
  }, [coupons]);

  const toggleActive = async (coupon) => {
    if (togglingId === coupon.id) return;
    setTogglingId(coupon.id);
    try {
      const { data } = await client.put(`/admin/coupons/${coupon.id}`, {
        is_active: !coupon.is_active,
      });
      setCoupons((list) => list.map((c) => (c.id === coupon.id ? data : c)));
      toast.success(`${coupon.code} ${data.is_active ? "is live" : "is paused"}`);
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setTogglingId(null);
    }
  };

  const confirmDelete = async () => {
    setDeleteBusy(true);
    try {
      await client.delete(`/admin/coupons/${deleting.id}`);
      toast.info(`Retired ${deleting.code}`);
      setDeleting(null);
      await load();
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setDeleteBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header — one primary action, one stable height */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold">Promotions</h1>
          <p className="mt-0.5 text-sm opacity-60">
            Coupon codes, their limits and their honest usage — every basket re-checks the rules at checkout.
          </p>
        </div>
        <button
          type="button"
          className="btn btn-primary btn-sm min-w-40"
          onClick={() => setDrawer({})}
        >
          <Plus size={16} /> New promotion
        </button>
      </div>

      {/* Summary — four fixed-height chips */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatChip label="Active" value={summary.active} tone="text-success" />
        <StatChip label="Paused" value={summary.paused} />
        <StatChip label="One per customer" value={summary.onceEach} />
        <StatChip label="Redemptions" value={summary.redemptions} tone="text-secondary" />
      </div>

      {/* Filter row — search grows, the segmented control keeps its width */}
      <div className="flex flex-wrap items-center gap-3">
        <label className="input input-bordered input-sm flex min-w-52 flex-1 items-center gap-2">
          <Search size={15} className="shrink-0 opacity-40" />
          <input
            className="grow"
            placeholder="Search code or note"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search promotions"
          />
        </label>
        <div className="join" role="group" aria-label="Filter by status">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.value}
              type="button"
              className={`btn btn-sm join-item min-w-20 ${status === f.value ? "btn-secondary text-secondary-content" : "btn-ghost border border-base-300"}`}
              onClick={() => setStatus(f.value)}
              aria-pressed={status === f.value}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {/* The coupon table — reserved height, aligned columns whatever the filter shows */}
      <div className="glass min-h-64 overflow-x-auto rounded-box shadow-sm ring-1 ring-base-300/60">
        {!loaded ? (
          <div className="flex h-64 items-center justify-center">
            <span className="loading loading-spinner loading-md text-secondary" />
            <span className="ml-3 text-sm opacity-60">Fetching the coupon tin…</span>
          </div>
        ) : visible.length === 0 ? (
          <div className="flex h-64 flex-col items-center justify-center gap-3 text-center">
            <Percent size={28} className="opacity-30" />
            <p className="text-sm opacity-60">
              {coupons.length === 0
                ? "No promotions yet — create the first one."
                : "Nothing matches this filter."}
            </p>
          </div>
        ) : (
          <table className="table table-sm">
            <thead>
              <tr className="text-xs uppercase opacity-50">
                <th>Code</th>
                <th className="text-right">Discount</th>
                <th className="text-right">Minimum</th>
                <th className="text-right">Redemptions</th>
                <th>Note</th>
                <th>Status</th>
                <th className="hidden text-right md:table-cell">Added</th>
                <th className="text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((c) => (
                <tr key={c.id} className={c.is_active ? "hover:bg-base-200/50" : "opacity-55"}>
                  <td className="max-w-56">
                    <span className="inline-flex items-center gap-2">
                      <span className="truncate font-mono font-bold" title={c.code}>{c.code}</span>
                    </span>
                  </td>
                  <td className="whitespace-nowrap text-right font-bold">{c.percent_off}% off</td>
                  <td className="whitespace-nowrap text-right">
                    {c.min_subtotal == null ? (
                      <span className="opacity-50">none</span>
                    ) : c.min_subtotal_approved ? (
                      `${money(c.min_subtotal)}+`
                    ) : (
                      // A number recorded before the EUR cutover — a dollar
                      // numeral, not an approved euro threshold. Shoppers
                      // can't use the code until it is re-saved from the editor.
                      <span className="inline-flex items-center gap-1 text-warning">
                        {money(c.min_subtotal)}
                        <span className="badge badge-warning badge-xs whitespace-nowrap">pending review</span>
                      </span>
                    )}
                  </td>
                  <td className="text-right tabular-nums">{c.redemptions ?? 0}</td>
                  <td className="max-w-48 truncate text-sm opacity-70" title={c.description ?? ""}>
                    {c.description ?? "—"}
                  </td>
                  <td>
                    <span className="inline-flex items-center gap-2">
                      <input
                        type="checkbox"
                        className="toggle toggle-secondary toggle-sm"
                        checked={c.is_active}
                        onChange={() => toggleActive(c)}
                        disabled={togglingId === c.id}
                        aria-label={`Toggle ${c.code}`}
                      />
                      <span className={`badge badge-sm ${c.is_active ? "badge-success" : "badge-ghost"}`}>
                        {c.is_active ? "live" : "paused"}
                      </span>
                    </span>
                  </td>
                  <td className="hidden text-right text-xs whitespace-nowrap opacity-60 md:table-cell">{formatDate(c.created_at)}</td>
                  <td className="text-right whitespace-nowrap">
                    <button
                      type="button"
                      className="btn btn-ghost btn-xs min-w-16"
                      onClick={() => setDrawer(c)}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="btn btn-ghost btn-xs min-w-16 text-error"
                      onClick={() => setDeleting(c)}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Editor — floats over everything; the page beneath never moves */}
      {drawer !== null && (
        <CouponDrawer
          coupon={drawer.id ? drawer : null}
          onClose={() => setDrawer(null)}
          onSaved={() => {
            setDrawer(null);
            load();
          }}
        />
      )}

      {/* Delete confirm */}
      {deleting && (
        <ConfirmDialog
          title={`Retire ${deleting.code}?`}
          confirmLabel="Yes, retire it"
          busy={deleteBusy}
          onConfirm={confirmDelete}
          onCancel={() => setDeleting(null)}
        >
          Customers can no longer apply it. Orders already placed with the code
          are untouched.
        </ConfirmDialog>
      )}
    </div>
  );
}
