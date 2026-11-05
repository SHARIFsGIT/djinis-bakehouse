import { useEffect, useState } from "react";
import client, { apiError } from "../../api/client";
import { formatDate } from "../../lib/format";
import { REDEMPTION_RULES, oncePerCustomerFromRule, redemptionRule } from "../../lib/coupons";
import { useToast } from "../../context/ToastContext";
import ConfirmDialog from "../../components/ConfirmDialog";
import { Loading } from "../../components/Loading";

const EMPTY_FORM = { code: "", percent_off: "", description: "", once_per_customer: false };

export default function Promotions() {
  const toast = useToast();
  const [coupons, setCoupons] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(null); // coupon row being renamed inline
  const [deleting, setDeleting] = useState(null);
  const [ruleSavingId, setRuleSavingId] = useState(null); // coupon whose rule select is mid-save

  const load = () =>
    client.get("/admin/coupons").then(({ data }) => setCoupons(data));

  useEffect(() => {
    load().catch((e) => toast.error(apiError(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const create = async (e) => {
    e.preventDefault();
    if (busy) return; // one create in flight, whatever re-triggered the submit
    setBusy(true);
    try {
      await client.post("/admin/coupons", {
        code: form.code.trim(),
        percent_off: Number(form.percent_off),
        description: form.description.trim() || null,
        once_per_customer: form.once_per_customer,
      });
      toast.success(`Coupon ${form.code.trim().toUpperCase()} is live`);
      setForm(EMPTY_FORM);
      // The list refresh rides along; the button doesn't wait for it.
      load().catch((e) => toast.error(apiError(e)));
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setBusy(false);
    }
  };

  const toggleActive = async (coupon) => {
    try {
      await client.put(`/admin/coupons/${coupon.id}`, { is_active: !coupon.is_active });
      await load();
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  const changeRedemption = async (coupon, oncePerCustomer) => {
    if (oncePerCustomer === coupon.once_per_customer || ruleSavingId === coupon.id) return;
    const rule = REDEMPTION_RULES.find((r) => r.once_per_customer === oncePerCustomer);
    setRuleSavingId(coupon.id);
    // Show the admin's pick right away; the server's answer settles it.
    setCoupons((list) =>
      list.map((c) => (c.id === coupon.id ? { ...c, once_per_customer: oncePerCustomer } : c)),
    );
    try {
      const { data } = await client.put(`/admin/coupons/${coupon.id}`, {
        once_per_customer: oncePerCustomer,
      });
      setCoupons((list) => list.map((c) => (c.id === coupon.id ? data : c)));
      toast.success(`${coupon.code} — ${rule.label.toLowerCase()}`);
    } catch (err) {
      // Back to whatever the server last said this row was.
      setCoupons((list) =>
        list.map((c) => (c.id === coupon.id ? { ...c, once_per_customer: coupon.once_per_customer } : c)),
      );
      toast.error(apiError(err));
    } finally {
      setRuleSavingId(null);
    }
  };

  const savePercent = async (coupon) => {
    const value = Number(editing.value);
    if (!value || value < 1 || value > 100) {
      toast.error("Percent off must be between 1 and 100");
      return;
    }
    try {
      await client.put(`/admin/coupons/${coupon.id}`, { percent_off: value });
      toast.success(`${coupon.code} now takes ${value}% off`);
      setEditing(null);
      await load();
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  const confirmDelete = async () => {
    try {
      await client.delete(`/admin/coupons/${deleting.id}`);
      toast.info(`Retired ${deleting.code}`);
      setDeleting(null);
      await load();
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  return (
    <div className="space-y-6">
      <p className="text-sm opacity-70">
        Coupons take a percent off the subtotal at checkout — tax is recalculated after the discount.
        Existing orders keep whatever was applied.
      </p>

      {/* New coupon */}
      <form
        onSubmit={create}
        className="grid gap-3 rounded-box glass p-4 shadow-sm ring-1 ring-base-300/60 sm:grid-cols-[160px_120px_minmax(0,1fr)_200px_auto] sm:items-end"
      >
        <div>
          <label className="label text-xs">Code *</label>
          <input
            className="input input-bordered w-full font-mono uppercase"
            required
            minLength={3}
            maxLength={50}
            pattern="[A-Za-z0-9]+"
            title="Letters and numbers only"
            placeholder="WELCOME10"
            value={form.code}
            onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))}
          />
        </div>
        <div>
          <label className="label text-xs">Percent off *</label>
          <input
            type="number"
            className="input input-bordered w-full"
            required
            min={1}
            max={100}
            placeholder="10"
            value={form.percent_off}
            onChange={(e) => setForm((f) => ({ ...f, percent_off: e.target.value }))}
          />
        </div>
        <div className="min-w-0">
          <label
            className="label text-xs"
            title="Optional. Shows beside the code on the voucher board — a handy reminder at the counter of what the code is for."
          >
            Note for the counter
          </label>
          <input
            className="input input-bordered w-full"
            maxLength={255}
            placeholder="First-order welcome discount"
            value={form.description}
            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
          />
          <p className="mt-1 text-xs opacity-60">Optional — shows beside the code on the voucher board.</p>
        </div>
        <div>
          <label
            className="label text-xs"
            title="How often each customer may use this code — a 1-per-customer code frees up again if that order is cancelled"
          >
            Customer limit
          </label>
          <select
            className="select select-bordered w-full"
            value={redemptionRule(form.once_per_customer)}
            onChange={(e) =>
              setForm((f) => ({ ...f, once_per_customer: oncePerCustomerFromRule(e.target.value) }))
            }
          >
            {REDEMPTION_RULES.map((rule) => (
              <option key={rule.value} value={rule.value}>
                {rule.label}
              </option>
            ))}
          </select>
        </div>
        <button type="submit" className="btn btn-primary min-w-36" disabled={busy}>
          {busy && <span className="loading loading-spinner loading-xs" />}
          Add coupon
        </button>
      </form>

      {/* List */}
      {coupons === null ? (
        <Loading label="Fetching the coupon tin…" />
      ) : coupons.length === 0 ? (
        <p className="rounded-box glass p-8 text-center text-sm opacity-60 ring-1 ring-base-300/60">
          No coupons yet — add one above.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-box glass shadow-sm ring-1 ring-base-300/60">
          <table className="table table-sm">
            <thead>
              <tr className="text-xs uppercase opacity-50">
                <th>Code</th>
                <th className="text-right">Discount</th>
                <th>Note</th>
                <th>Status</th>
                <th className="text-right">Added</th>
                <th className="text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {coupons.map((c) => (
                <tr key={c.id} className={c.is_active ? "hover:bg-base-200/50" : "opacity-55"}>
                  <td className="font-mono font-bold">
                    {c.code}
                    <select
                      className="select select-bordered select-xs ml-2 font-sans font-normal"
                      value={redemptionRule(c.once_per_customer)}
                      disabled={ruleSavingId === c.id}
                      title="How often each customer may use this code — a 1-per-customer code frees up again if that order is cancelled"
                      aria-label={`Customer limit for ${c.code}`}
                      onChange={(e) => changeRedemption(c, oncePerCustomerFromRule(e.target.value))}
                    >
                      {REDEMPTION_RULES.map((rule) => (
                        <option key={rule.value} value={rule.value}>
                          {rule.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="text-right">
                    {editing?.id === c.id ? (
                      <span className="inline-flex items-center gap-1">
                        <input
                          type="number"
                          className="input input-bordered input-xs w-16"
                          min={1}
                          max={100}
                          value={editing.value}
                          onChange={(e) => setEditing({ id: c.id, value: e.target.value })}
                          autoFocus
                        />
                        <button type="button" className="btn btn-secondary btn-xs" onClick={() => savePercent(c)}>
                          Save
                        </button>
                        <button type="button" className="btn btn-ghost btn-xs" onClick={() => setEditing(null)}>
                          Cancel
                        </button>
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="btn btn-ghost btn-xs font-bold"
                        title="Change the percent"
                        onClick={() => setEditing({ id: c.id, value: String(c.percent_off) })}
                      >
                        {c.percent_off}% off
                      </button>
                    )}
                  </td>
                  <td className="max-w-64 truncate text-sm opacity-70">{c.description ?? "—"}</td>
                  <td>
                    <input
                      type="checkbox"
                      className="toggle toggle-secondary toggle-sm"
                      checked={c.is_active}
                      onChange={() => toggleActive(c)}
                      aria-label={`Toggle ${c.code}`}
                    />
                    <span className="ml-2 text-xs opacity-60">{c.is_active ? "live" : "paused"}</span>
                  </td>
                  <td className="text-right text-xs opacity-60">{formatDate(c.created_at)}</td>
                  <td className="text-right">
                    <button
                      type="button"
                      className="btn btn-ghost btn-xs text-error"
                      onClick={() => setDeleting(c)}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Delete confirm */}
      {deleting && (
        <ConfirmDialog
          title={`Retire ${deleting.code}?`}
          confirmLabel="Yes, retire it"
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
