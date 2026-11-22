import { useEffect, useState } from "react";
import client, { apiError } from "../../api/client";
import { useToast } from "../../context/ToastContext";
import ConfirmDialog from "../../components/ConfirmDialog";
import EmptyState from "../../components/EmptyState";
import { LoadError, Loading } from "../../components/Loading";

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

/* [key, label, required, grid span, placeholder, autocomplete, max length].
   The lengths are the server's own (schemas.AddressBase) — the box stops
   where the API would, instead of letting a long line be typed and then
   refused. Autocomplete tokens let the browser offer the customer's saved
   address in one tap. */
const FIELDS = [
  ["label", "Label (Home, Work…)", true, "", "", "off", 50],
  ["recipient", "Recipient name", true, "", "", "name", 150],
  ["phone", "Phone", false, "", "", "tel", 30],
  ["line1", "Street address", true, "sm:col-span-2", "", "address-line1", 255],
  ["line2", "Apartment, suite… (optional)", false, "sm:col-span-2", "", "address-line2", 255],
  ["city", "City", true, "", "", "address-level2", 100],
  ["state", "State / region", true, "", "", "address-level1", 100],
  ["postal_code", "Postal code", true, "", "", "postal-code", 20],
  // No default country — the customer writes where they actually live,
  // nothing is pre-filled on their behalf.
  ["country", "Country", true, "", "Germany", "country-name", 100],
];

export default function Addresses() {
  const toast = useToast();
  const [addresses, setAddresses] = useState(null);
  const [failed, setFailed] = useState(null);
  const [editing, setEditing] = useState(null); // null | {} (new) | address (edit)
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState(null); // the address awaiting a yes
  const [removeBusy, setRemoveBusy] = useState(false);

  const load = () => {
    setFailed(null);
    return client
      .get("/auth/addresses")
      .then(({ data }) => setAddresses(data))
      .catch((e) => setFailed(apiError(e, "Your address book didn't load.")));
  };

  useEffect(() => {
    load();
  }, []);

  const setDefault = async (address) => {
    try {
      await client.put(`/auth/addresses/${address.id}`, { is_default: true });
      await load();
      toast.success(`“${address.label}” is now your default`);
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  const remove = async () => {
    if (removeBusy || !removing) return;
    setRemoveBusy(true);
    try {
      await client.delete(`/auth/addresses/${removing.id}`);
      await load();
      toast.info(`Removed “${removing.label}”`);
      setRemoving(null);
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setRemoveBusy(false);
    }
  };

  const save = async (e) => {
    e.preventDefault();
    if (busy) return;
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

  if (failed && addresses === null) return <LoadError message={failed} onRetry={load} />;
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
            {FIELDS.map(([key, label, required, span, placeholder, autoComplete, maxLength]) => (
              <div key={key} className={span}>
                {/* Tied to its input by id — a screen reader announces the
                    field, and a tap on the label focuses it. */}
                <label className="label text-xs" htmlFor={`book-${key}`}>{label}</label>
                <input
                  id={`book-${key}`}
                  className="input input-bordered w-full"
                  required={required}
                  maxLength={maxLength}
                  placeholder={placeholder}
                  autoComplete={autoComplete}
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
                  aria-label={`Edit the address “${a.label}”`}
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
                  onClick={() => setRemoving(a)}
                  aria-label={`Remove the address “${a.label}”`}
                >
                  Remove
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {removing && (
        <ConfirmDialog
          title={`Remove “${removing.label}”?`}
          confirmLabel="Yes, remove it"
          cancelLabel="Keep it"
          busy={removeBusy}
          onConfirm={remove}
          onCancel={() => setRemoving(null)}
        >
          {removing.line1}, {removing.postal_code} {removing.city}. Orders already placed keep
          their own copy of the address — only your book changes.
        </ConfirmDialog>
      )}
    </div>
  );
}
