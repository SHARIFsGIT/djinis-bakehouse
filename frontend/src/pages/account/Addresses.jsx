import { useEffect, useState } from "react";
import client, { apiError } from "../../api/client";
import { useToast } from "../../context/ToastContext";
import EmptyState from "../../components/EmptyState";
import { Loading } from "../../components/Loading";

const EMPTY_FORM = {
  label: "Home",
  recipient: "",
  phone: "",
  line1: "",
  line2: "",
  city: "",
  state: "",
  postal_code: "",
  country: "",
  is_default: false,
};

const FIELDS = [
  ["label", "Label (Home, Work…)", true, "", ""],
  ["recipient", "Recipient name", true, "", ""],
  ["phone", "Phone", false, "", ""],
  ["line1", "Street address", true, "sm:col-span-2", ""],
  ["line2", "Apartment, suite… (optional)", false, "sm:col-span-2", ""],
  ["city", "City", true, "", ""],
  ["state", "State", true, "", ""],
  ["postal_code", "ZIP / postal code", true, "", ""],
  // No default country — the customer writes where they actually live,
  // nothing is pre-filled on their behalf.
  ["country", "Country", true, "", "Germany"],
];

export default function Addresses() {
  const toast = useToast();
  const [addresses, setAddresses] = useState(null);
  const [editing, setEditing] = useState(null); // null | {} (new) | address (edit)
  const [busy, setBusy] = useState(false);

  const load = () => client.get("/auth/addresses").then(({ data }) => setAddresses(data));

  useEffect(() => {
    load();
  }, []);

  const setDefault = async (address) => {
    try {
      await client.put(`/auth/addresses/${address.id}`, { ...address, is_default: true });
      await load();
      toast.success(`“${address.label}” is now your default`);
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  const remove = async (address) => {
    try {
      await client.delete(`/auth/addresses/${address.id}`);
      await load();
      toast.info(`Removed “${address.label}”`);
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      if (editing.id) {
        await client.put(`/auth/addresses/${editing.id}`, editing.form);
        toast.success("Address updated");
      } else {
        await client.post("/auth/addresses", editing.form);
        toast.success("Address added");
      }
      setEditing(null);
      await load();
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setBusy(false);
    }
  };

  if (addresses === null) return <Loading label="Fetching your address book…" />;

  return (
    <div>
      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 className="font-display text-2xl font-semibold">Address book</h2>
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={() => setEditing({ form: { ...EMPTY_FORM, is_default: addresses.length === 0 } })}
        >
          Add address
        </button>
      </div>

      {editing && (
        <form
          onSubmit={save}
          className="rise-in mb-6 rounded-box glass p-5 shadow-md ring-1 ring-base-300/60"
        >
          <p className="font-display mb-4 font-semibold">
            {editing.id ? `Edit “${editing.form.label}”` : "New address"}
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            {FIELDS.map(([key, label, required, span, placeholder]) => (
              <div key={key} className={span}>
                <label className="label text-xs">{label}</label>
                <input
                  className="input input-bordered w-full"
                  required={required}
                  maxLength={key === "label" ? 50 : 255}
                  placeholder={placeholder}
                  value={editing.form[key] ?? ""}
                  onChange={(e) =>
                    setEditing((s) => ({ ...s, form: { ...s.form, [key]: e.target.value } }))
                  }
                />
              </div>
            ))}
            <label className="label sm:col-span-2 cursor-pointer justify-start gap-3">
              <input
                type="checkbox"
                className="toggle toggle-secondary toggle-sm"
                checked={editing.form.is_default}
                onChange={(e) =>
                  setEditing((s) => ({ ...s, form: { ...s.form, is_default: e.target.checked } }))
                }
              />
              <span className="label-text text-sm">Make this my default address</span>
            </label>
          </div>
          <div className="mt-4 flex gap-2">
            <button type="submit" className="btn btn-secondary btn-sm text-secondary-content" disabled={busy}>
              {busy ? "Saving…" : "Save address"}
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing(null)}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {addresses.length === 0 ? (
        <EmptyState
          title="No addresses yet"
          hint="Add one and checkout becomes a two-click affair."
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {addresses.map((a) => (
            <div
              key={a.id}
              className={`rounded-box glass p-5 shadow-sm ring-1 transition ${
                a.is_default ? "ring-2 ring-secondary" : "ring-base-300/60"
              }`}
            >
              <div className="flex items-center gap-2">
                <p className="font-display text-lg font-semibold">{a.label}</p>
                {a.is_default && <span className="badge badge-secondary badge-xs">default</span>}
              </div>
              <p className="mt-2 text-sm opacity-80">
                {a.recipient}{a.phone ? ` · ${a.phone}` : ""}<br />
                {a.line1}{a.line2 ? `, ${a.line2}` : ""}<br />
                {a.city}, {a.state} {a.postal_code}, {a.country}
              </p>
              <div className="dash-divider mt-4 flex flex-wrap gap-2 pt-4">
                <button
                  type="button"
                  className="btn btn-ghost btn-xs"
                  onClick={() => setEditing({ id: a.id, form: { ...a } })}
                >
                  Edit
                </button>
                {!a.is_default && (
                  <button type="button" className="btn btn-ghost btn-xs" onClick={() => setDefault(a)}>
                    Make default
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn-ghost btn-xs text-error"
                  onClick={() => remove(a)}
                >
                  Remove
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
