import { useMemo, useState } from "react";
import client, { apiError } from "../api/client";
import { EVENT_TYPES, isoDate } from "../lib/format";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import EmptyState from "../components/EmptyState";

const EMPTY_FORM = {
  name: "",
  email: "",
  phone: "",
  event_type: "birthday",
  event_date: "",
  guests: "",
  items_note: "",
};

export default function BulkOrder() {
  const { user } = useAuth();
  const toast = useToast();
  const [form, setForm] = useState({
    ...EMPTY_FORM,
    name: user ? `${user.first_name} ${user.last_name}`.trim() : "",
    email: user?.email ?? "",
  });
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);

  const minDate = useMemo(() => {
    const d = new Date();
    d.setDate(d.getDate() + 1); // the ovens need at least a day's notice
    return isoDate(d);
  }, []);

  const setField = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  const submit = async (e) => {
    e.preventDefault();
    setSending(true);
    try {
      await client.post("/bulk-orders", {
        ...form,
        guests: Number(form.guests),
      });
      setSent(true);
      toast.success("Enquiry sent — we'll be in touch within a day");
    } catch (err) {
      toast.error(apiError(err, "Couldn't send the enquiry"));
    } finally {
      setSending(false);
    }
  };

  if (sent) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-12">
        <EmptyState
          title="Enquiry received"
          hint="Djini will call or write back within one working day with a quote, portions and delivery timing. Nothing is charged until you say yes."
        >
          <button type="button" className="btn btn-primary" onClick={() => setSent(false)}>
            Send another enquiry
          </button>
        </EmptyState>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl px-4 py-12">
      <p className="text-xs font-bold tracking-[0.25em] text-secondary uppercase">
        Events &amp; catering
      </p>
      <h1 className="font-display mt-2 text-4xl font-semibold md:text-5xl">
        Feeding a crowd?
      </h1>
      <p className="mt-3 opacity-75">
        Tell us the date and the crowd — we quote within a working day.
      </p>

      <form
        onSubmit={submit}
        className="rounded-box glass mt-8 p-6 shadow-sm ring-1 ring-base-300/60 md:p-8"
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="label text-xs" htmlFor="bulk-name">
              Your name
            </label>
            <input
              id="bulk-name"
              className="input input-bordered w-full"
              required
              maxLength={150}
              value={form.name}
              onChange={(e) => setField("name", e.target.value)}
            />
          </div>
          <div>
            <label className="label text-xs" htmlFor="bulk-email">
              Email
            </label>
            <input
              id="bulk-email"
              type="email"
              className="input input-bordered w-full"
              required
              maxLength={255}
              value={form.email}
              onChange={(e) => setField("email", e.target.value)}
            />
          </div>
          <div>
            <label className="label text-xs" htmlFor="bulk-type">
              The occasion
            </label>
            <select
              id="bulk-type"
              className="select select-bordered w-full"
              value={form.event_type}
              onChange={(e) => setField("event_type", e.target.value)}
            >
              {EVENT_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label text-xs" htmlFor="bulk-date">
              Event date
            </label>
            <input
              id="bulk-date"
              type="date"
              className="input input-bordered w-full"
              required
              min={minDate}
              value={form.event_date}
              onChange={(e) => setField("event_date", e.target.value)}
            />
          </div>
          <div>
            <label className="label text-xs" htmlFor="bulk-guests">
              How many guests?
            </label>
            <input
              id="bulk-guests"
              type="number"
              className="input input-bordered w-full"
              required
              min="1"
              max="2000"
              value={form.guests}
              onChange={(e) => setField("guests", e.target.value)}
            />
          </div>
          <div>
            <label className="label text-xs" htmlFor="bulk-phone">
              Phone (optional)
            </label>
            <input
              id="bulk-phone"
              type="tel"
              className="input input-bordered w-full"
              maxLength={30}
              value={form.phone}
              onChange={(e) => setField("phone", e.target.value)}
            />
          </div>
          <div className="sm:col-span-2">
            <label className="label text-xs" htmlFor="bulk-items">
              What did you have in mind? (optional)
            </label>
            <textarea
              id="bulk-items"
              rows={3}
              maxLength={2000}
              className="textarea textarea-bordered w-full"
              placeholder="60 croffles, 3 garden focaccias, delivery by 2pm…"
              value={form.items_note}
              onChange={(e) => setField("items_note", e.target.value)}
            />
          </div>
        </div>

        <div className="mt-6 flex flex-wrap items-center gap-4">
          <button
            type="submit"
            className="btn btn-primary btn-lg"
            disabled={sending}
          >
            {sending ? <span className="loading loading-spinner loading-sm" /> : null}
            {sending ? "Sending…" : "Send the enquiry"}
          </button>
          <p className="text-xs opacity-60">
            No account needed · nothing charged until you approve the quote
          </p>
        </div>
      </form>

      <p className="mt-6 text-center text-xs tracking-wide opacity-55">
        Tell us the date · We quote within a day · Baked the same morning as your event
      </p>
    </div>
  );
}
