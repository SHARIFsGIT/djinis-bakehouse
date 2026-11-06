import { useEffect, useMemo, useRef, useState } from "react";
import {
  Ban,
  Copy,
  Eye,
  MailCheck,
  MailX,
  Megaphone,
  Search,
  Send,
  TicketPercent,
} from "lucide-react";
import client, { apiError } from "../../api/client";
import { formatDateTime, money } from "../../lib/format";
import { useToast } from "../../context/ToastContext";
import { Loading } from "../../components/Loading";

/* The desk's classification words and the measured loyalty tiers share a
   vocabulary but not a meaning — campaigns may select on either, and the
   chips below say which list they came from. */
const WORDS = ["vip", "loyal", "lapsed", "regular"];
const MEASURED = ["vip", "loyal", "lapsed"];
const label = (v) => (v === "vip" ? "VIP" : v.charAt(0).toUpperCase() + v.slice(1));

const CAMPAIGN_STATUS = {
  draft: "badge-ghost",
  sending: "badge-warning text-warning-content",
  sent: "badge-success text-success-content",
};

function StatChip({ label, value, tone = "" }) {
  return (
    <div className="glass flex min-h-17 items-center justify-between gap-3 rounded-box px-4 py-3 ring-1 ring-base-300/60">
      <span className="text-xs font-bold tracking-widest uppercase opacity-55">{label}</span>
      <span className={`font-display text-2xl font-bold ${tone}`}>{value}</span>
    </div>
  );
}

/* Search the roll, pick one customer — used by single-recipient campaigns
   and by voucher minting. Shows consent at a glance: no tick, no email. */
function CustomerPicker({ picked, onPick, idPrefix }) {
  const [term, setTerm] = useState("");
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const seq = useRef(0);

  useEffect(() => {
    const needle = term.trim();
    if (needle.length < 2) {
      setResults([]);
      setOpen(false);
      return;
    }
    const mine = ++seq.current;
    setSearching(true);
    const timer = setTimeout(() => {
      client
        .get("/admin/users", { params: { search: needle, per_page: 5 } })
        .then(({ data }) => {
          if (mine !== seq.current) return;
          setResults(data.items);
          setOpen(true);
        })
        .catch(() => {})
        .finally(() => mine === seq.current && setSearching(false));
    }, 300);
    return () => clearTimeout(timer);
  }, [term]);

  return (
    <div className="relative">
      {picked ? (
        <div className="flex items-center justify-between gap-2 rounded-box border border-secondary/40 bg-secondary/10 px-3 py-2">
          <span className="flex min-w-0 items-center gap-2 text-sm font-semibold">
            {picked.marketing_consent_at ? (
              <MailCheck size={14} className="shrink-0 text-success" aria-label="Has marketing consent" />
            ) : (
              <MailX size={14} className="shrink-0 opacity-40" aria-label="No marketing consent" />
            )}
            <span className="truncate">
              {picked.first_name} {picked.last_name}
              <span className="ml-1 font-normal opacity-60">{picked.email}</span>
            </span>
          </span>
          <button
            type="button"
            className="btn btn-ghost btn-xs"
            onClick={() => onPick(null)}
            aria-label="Choose someone else"
          >
            Change
          </button>
        </div>
      ) : (
        <>
          <label className="label text-xs" htmlFor={`${idPrefix}-search`}>Find the customer</label>
          <div className="relative">
            <Search size={14} className="absolute top-1/2 left-3 -translate-y-1/2 opacity-40" aria-hidden="true" />
            <input
              id={`${idPrefix}-search`}
              className="input input-bordered input-sm w-full pl-8"
              placeholder="Name, email or phone…"
              value={term}
              onChange={(e) => setTerm(e.target.value)}
              autoComplete="off"
            />
            {searching && (
              <span className="loading loading-spinner loading-xs absolute top-1/2 right-3 -translate-y-1/2 opacity-50" />
            )}
          </div>
          {open && results.length > 0 && (
            <ul className="absolute z-30 mt-1 w-full overflow-hidden rounded-box border border-base-300 bg-base-100 shadow-lg">
              {results.map((u) => (
                <li key={u.id}>
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-base-200"
                    onClick={() => {
                      onPick(u);
                      setOpen(false);
                      setTerm("");
                    }}
                  >
                    {u.marketing_consent_at ? (
                      <MailCheck size={13} className="shrink-0 text-success" aria-label="Has marketing consent" />
                    ) : (
                      <MailX size={13} className="shrink-0 opacity-40" aria-label="No marketing consent" />
                    )}
                    <span className="min-w-0 truncate">
                      <span className="font-semibold">{u.first_name} {u.last_name}</span>
                      <span className="ml-1 opacity-60">{u.email}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {open && results.length === 0 && !searching && (
            <p className="absolute z-30 mt-1 w-full rounded-box border border-base-300 bg-base-100 px-3 py-2 text-xs opacity-60 shadow-lg">
              Nobody matches “{term.trim()}”.
            </p>
          )}
        </>
      )}
    </div>
  );
}

/* One row of toggles — checked values narrow the audience, none = any. */
function ChipRow({ title, options, values, onToggle }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="mr-1 text-xs font-semibold uppercase tracking-widest opacity-50">{title}</span>
      {options.map((opt) => {
        const on = values.includes(opt);
        return (
          <button
            key={opt}
            type="button"
            onClick={() => onToggle(opt)}
            className={`btn btn-xs rounded-full ${on ? "btn-secondary text-secondary-content" : "btn-ghost border border-base-300"}`}
            aria-pressed={on}
          >
            {label(opt)}
          </button>
        );
      })}
    </div>
  );
}

const audienceLabel = (c) => {
  if (c.recipient_user_id !== null && c.recipient_user_id !== undefined) {
    return `Just ${c.recipient_name ?? "one customer"}`;
  }
  const parts = [];
  if (c.segment?.classifications?.length) {
    parts.push(`desk: ${c.segment.classifications.map(label).join(", ")}`);
  }
  if (c.segment?.loyalty?.length) {
    parts.push(`loyalty: ${c.segment.loyalty.map(label).join(", ")}`);
  }
  return parts.length ? parts.join(" · ") : "Everyone with consent";
};

/* The campaign read-back drawer: body, audience and every delivery with its
   reason — the holds are as interesting as the sends. */
function CampaignDrawer({ campaignId, onClose, onSent }) {
  const toast = useToast();
  const [detail, setDetail] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const refresh = () =>
    client
      .get(`/admin/marketing/campaigns/${campaignId}`)
      .then(({ data }) => setDetail(data))
      .catch((err) => toast.error(apiError(err)));

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaignId]);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const send = async () => {
    setBusy(true);
    try {
      const { data } = await client.post(`/admin/marketing/campaigns/${campaignId}/send`);
      toast.success(
        data.status === "sent" ? "Campaign sent" : "Campaign is going out — delivery rows are being written",
      );
      onSent();
      // The background send flips the row to "sent" moments later; look again.
      setTimeout(refresh, 1500);
      refresh();
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  const byStatus = useMemo(() => {
    const rows = detail?.deliveries ?? [];
    return {
      sent: rows.filter((d) => d.status === "sent"),
      suppressed: rows.filter((d) => d.status === "suppressed"),
      failed: rows.filter((d) => d.status === "failed"),
      queued: rows.filter((d) => d.status === "queued"),
    };
  }, [detail]);

  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label="Campaign">
      <div className="absolute inset-0 bg-black/40" onClick={() => !busy && onClose()} />
      <div className="absolute inset-y-0 right-0 flex w-full max-w-md flex-col bg-base-100 shadow-2xl">
        <div className="flex items-start justify-between border-b border-base-300/70 px-6 py-4">
          <div>
            <h2 className="font-display text-xl font-semibold">{detail?.name ?? "…"}</h2>
            <p className="text-xs opacity-60">
              {detail ? `${detail.subject} · ${audienceLabel(detail)}` : "Loading…"}
            </p>
          </div>
          <button type="button" className="btn btn-ghost btn-sm btn-square" onClick={onClose} disabled={busy} aria-label="Close">
            ✕
          </button>
        </div>

        {detail === null ? (
          <div className="flex-1 space-y-3 px-6 py-5" aria-label="Loading the campaign">
            <div className="skeleton h-4 w-full" />
            <div className="skeleton h-4 w-2/3" />
            <div className="skeleton h-20 w-full" />
          </div>
        ) : (
          <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5">
            {detail.status === "draft" && (
              <div className="rounded-box border border-base-300 bg-base-200/50 p-4">
                <p className="text-xs opacity-70">
                  Still a draft — nothing has been emailed. Sending resolves the audience this
                  instant (consent + suppression) and writes one delivery row per person, holds
                  included.
                </p>
                <div className="mt-3 flex gap-2">
                  {confirming ? (
                    <>
                      <button type="button" className="btn btn-error btn-sm" onClick={send} disabled={busy}>
                        {busy ? "Sending…" : "Yes, send it"}
                      </button>
                      <button type="button" className="btn btn-ghost btn-sm" onClick={() => setConfirming(false)} disabled={busy}>
                        Not yet
                      </button>
                    </>
                  ) : (
                    <button type="button" className="btn btn-primary btn-sm" onClick={() => setConfirming(true)}>
                      <Send size={14} /> Send now
                    </button>
                  )}
                </div>
              </div>
            )}

            <section>
              <h3 className="font-display text-sm font-bold uppercase tracking-widest opacity-55">The email</h3>
              <pre className="mt-2 rounded-box border border-base-300/70 bg-base-200/40 p-3 font-sans text-xs whitespace-pre-wrap">
                {detail.body}
              </pre>
              <p className="mt-1 text-[11px] opacity-50">
                Every copy carries the shop's footer with a working unsubscribe link.
              </p>
            </section>

            <section>
              <h3 className="font-display text-sm font-bold uppercase tracking-widest opacity-55">Delivery</h3>
              {detail.deliveries.length === 0 ? (
                <p className="mt-2 text-xs opacity-60">No delivery rows — this one hasn't been sent.</p>
              ) : (
                <div className="mt-2 space-y-3">
                  {[
                    ["sent", "Went out", byStatus.sent],
                    ["suppressed", "Held back", byStatus.suppressed],
                    ["failed", "Failed", byStatus.failed],
                    ["queued", "Queued", byStatus.queued],
                  ]
                    .filter(([, , rows]) => rows.length > 0)
                    .map(([key, title, rows]) => (
                      <div key={key}>
                        <p className="text-xs font-semibold">
                          {title} <span className="opacity-50">({rows.length})</span>
                        </p>
                        <ul className="mt-1 space-y-1">
                          {rows.map((d) => (
                            <li key={d.id} className="rounded border border-base-300/60 bg-base-200/30 px-2 py-1 text-xs">
                              <span className="font-semibold">{d.customer_name ?? d.email}</span>
                              {d.reason && <span className="opacity-60"> — {d.reason}</span>}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ))}
                </div>
              )}
            </section>
          </div>
        )}
      </div>
    </div>
  );
}

const EMPTY_CAMPAIGN = { name: "", subject: "", body: "" };

export default function Marketing() {
  const toast = useToast();
  const [overview, setOverview] = useState(null);
  const [campaigns, setCampaigns] = useState(null);
  const [vouchers, setVouchers] = useState(null);
  const [suppression, setSuppression] = useState(null);
  const [drawerId, setDrawerId] = useState(null);

  // The composer: who it addresses (segment chips or one customer), what it
  // says, and a preview that shows exactly who would be mailed and why not.
  const [form, setForm] = useState(EMPTY_CAMPAIGN);
  const [mode, setMode] = useState("segment");
  const [words, setWords] = useState([]);
  const [tiers, setTiers] = useState([]);
  const [recipient, setRecipient] = useState(null);
  const [preview, setPreview] = useState(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [saving, setSaving] = useState(false);

  // The voucher bench: exactly one discount kind, capped by the margin floor.
  const [voucher, setVoucher] = useState({
    picked: null,
    kind: "percent",
    percent: "",
    amount: "",
    expires: "",
    max: "",
    note: "",
  });
  const [mintBusy, setMintBusy] = useState(false);
  const [sendBusyId, setSendBusyId] = useState(null);

  const load = () => {
    client.get("/admin/marketing/overview").then(({ data }) => setOverview(data)).catch((e) => toast.error(apiError(e)));
    client.get("/admin/marketing/campaigns").then(({ data }) => setCampaigns(data)).catch((e) => toast.error(apiError(e)));
    // Personal vouchers are coupons restricted to one account — the coupon
    // tin carries them with their honest redemption counts.
    client
      .get("/admin/coupons")
      .then(({ data }) => setVouchers(data.filter((c) => c.restricted_to_user_id != null)))
      .catch((e) => toast.error(apiError(e)));
    client.get("/admin/marketing/suppression").then(({ data }) => setSuppression(data)).catch((e) => toast.error(apiError(e)));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const payload = () => ({
    name: form.name.trim(),
    subject: form.subject.trim(),
    body: form.body,
    ...(mode === "one"
      ? { recipient_user_id: recipient?.id ?? null }
      : {
          segment:
            words.length || tiers.length
              ? {
                  classifications: words.length ? words : undefined,
                  loyalty: tiers.length ? tiers : undefined,
                }
              : undefined,
        }),
  });

  const runPreview = () => {
    if (!form.name.trim() || !form.subject.trim() || !form.body) {
      toast.info("Give the campaign a name, a subject and a body first");
      return;
    }
    if (mode === "one" && !recipient) {
      toast.info("Pick who the message is for");
      return;
    }
    setPreviewBusy(true);
    client
      .post("/admin/marketing/campaigns/preview", payload())
      .then(({ data }) => setPreview(data))
      .catch((err) => toast.error(apiError(err)))
      .finally(() => setPreviewBusy(false));
  };

  const saveDraft = async (e) => {
    e.preventDefault();
    setSaving(true);
    try {
      await client.post("/admin/marketing/campaigns", payload());
      toast.success("Draft saved — send it when you're happy");
      setForm(EMPTY_CAMPAIGN);
      setWords([]);
      setTiers([]);
      setRecipient(null);
      setPreview(null);
      load();
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setSaving(false);
    }
  };

  const mint = async (e) => {
    e.preventDefault();
    setMintBusy(true);
    try {
      const { data } = await client.post("/admin/marketing/vouchers", {
        user_id: voucher.picked.id,
        ...(voucher.kind === "percent"
          ? { percent_off: Number(voucher.percent) }
          : { amount_off: Number(voucher.amount) }),
        expires_at: voucher.expires ? new Date(`${voucher.expires}T23:59:59`).toISOString() : null,
        max_redemptions: voucher.max ? Number(voucher.max) : null,
        note: voucher.note.trim() || null,
      });
      toast.success(`${data.code} minted for ${data.restricted_to_name ?? "its customer"}`);
      setVoucher((v) => ({ ...v, picked: null, percent: "", amount: "", expires: "", max: "", note: "" }));
      load();
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setMintBusy(false);
    }
  };

  const emailVoucher = async (coupon) => {
    if (sendBusyId === coupon.id) return;
    setSendBusyId(coupon.id);
    try {
      await client.post(`/admin/marketing/vouchers/${coupon.id}/send`);
      toast.success(`${coupon.code} is on its way to ${coupon.restricted_to_name ?? "its customer"}`);
      load();
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setSendBusyId(null);
    }
  };

  const copyCode = async (code) => {
    try {
      await navigator.clipboard.writeText(code);
      toast.success(`${code} copied`);
    } catch {
      toast.error("Couldn't reach the clipboard");
    }
  };

  if (campaigns === null || overview === null || vouchers === null || suppression === null) {
    return <Loading label="Opening the marketing desk…" />;
  }

  return (
    <div className="space-y-6">
      {/* The desk's headline numbers — consent is the whole game */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
        <StatChip label="Customers" value={overview.customers} />
        <StatChip label="Consenting" value={overview.consenting} tone="text-success" />
        <StatChip label="Suppressed" value={overview.suppressed} tone="text-error" />
        <StatChip label="Campaigns sent" value={overview.campaigns_sent} />
        <StatChip
          label="Voucher brake"
          value={
            overview.voucher_percent_cap != null
              ? `${overview.voucher_percent_cap}% / ${money(overview.voucher_fixed_cap)}`
              : "—"
          }
        />
      </div>

      {/* ------------------------------------------------- CAMPAIGNS ---- */}
      <section className="space-y-4">
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="font-display text-2xl font-semibold">Campaigns</h2>
          <p className="text-xs opacity-55">
            Only consenting customers are ever mailed — every send leaves its delivery record.
          </p>
        </div>

        <form onSubmit={saveDraft} className="space-y-4 rounded-box glass p-5 shadow-sm ring-1 ring-base-300/60">
          <div className="flex flex-wrap gap-2">
            {[
              ["segment", "A whole segment"],
              ["one", "One customer"],
            ].map(([value, text]) => (
              <button
                key={value}
                type="button"
                onClick={() => setMode(value)}
                className={`btn btn-sm ${mode === value ? "btn-secondary text-secondary-content" : "btn-ghost border border-base-300"}`}
                aria-pressed={mode === value}
              >
                {text}
              </button>
            ))}
          </div>

          {mode === "one" ? (
            <CustomerPicker picked={recipient} onPick={setRecipient} idPrefix="cmp" />
          ) : (
            <div className="space-y-2">
              <ChipRow
                title="Desk's word"
                options={WORDS}
                values={words}
                onToggle={(w) => setWords((list) => (list.includes(w) ? list.filter((x) => x !== w) : [...list, w]))}
              />
              <ChipRow
                title="Measured loyalty"
                options={MEASURED}
                values={tiers}
                onToggle={(t) => setTiers((list) => (list.includes(t) ? list.filter((x) => x !== t) : [...list, t]))}
              />
              <p className="text-[11px] opacity-50">
                Both rows narrow together; leave both empty to address every consenting customer.
              </p>
            </div>
          )}

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="label text-xs" htmlFor="cmp-name">Campaign name (desk only)</label>
              <input
                id="cmp-name"
                className="input input-bordered input-sm w-full"
                required
                maxLength={100}
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              />
            </div>
            <div>
              <label className="label text-xs" htmlFor="cmp-subject">Subject</label>
              <input
                id="cmp-subject"
                className="input input-bordered input-sm w-full"
                required
                maxLength={200}
                value={form.subject}
                onChange={(e) => setForm((f) => ({ ...f, subject: e.target.value }))}
              />
            </div>
          </div>
          <div>
            <label className="label text-xs" htmlFor="cmp-body">Body</label>
            <textarea
              id="cmp-body"
              className="textarea textarea-bordered textarea-sm w-full"
              required
              maxLength={10000}
              rows={5}
              placeholder={"Hi {{first_name}},\n\nThe stollen is back…"}
              value={form.body}
              onChange={(e) => setForm((f) => ({ ...f, body: e.target.value }))}
            />
            <p className="mt-1 text-[11px] opacity-50">
              <code>{"{{first_name}}"}</code> becomes each customer's own name — nobody sees anyone else's.
            </p>
          </div>

          {preview && (
            <div className="rounded-box border border-base-300 bg-base-200/50 p-3 text-sm">
              <p className="font-semibold">
                <Megaphone size={14} className="mr-1 inline opacity-60" aria-hidden="true" />
                Would reach {preview.recipients}{" "}
                {preview.recipients === 1 ? "customer" : "customers"}
                {preview.held.length > 0 && ` · ${preview.held.length} held back`}
              </p>
              {preview.held.length > 0 && (
                <ul className="mt-1 space-y-0.5 text-xs opacity-70">
                  {preview.held.slice(0, 6).map((h) => (
                    <li key={h.name}>{h.name} — {h.reason}</li>
                  ))}
                  {preview.held.length > 6 && <li>…and {preview.held.length - 6} more</li>}
                </ul>
              )}
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="btn btn-outline btn-sm"
              onClick={runPreview}
              disabled={previewBusy}
            >
              {previewBusy ? "Counting…" : "Preview the audience"}
            </button>
            <button type="submit" className="btn btn-primary btn-sm" disabled={saving}>
              {saving ? "Saving…" : "Save as draft"}
            </button>
          </div>
        </form>

        <div className="overflow-x-auto rounded-box glass shadow-sm ring-1 ring-base-300/60">
          <table className="table table-sm">
            <thead>
              <tr className="text-xs uppercase opacity-50">
                <th>Campaign</th>
                <th>Audience</th>
                <th>Status</th>
                <th className="text-right">Reach</th>
                <th>Created</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {campaigns.map((c) => (
                <tr key={c.id} className="hover:bg-base-200/50">
                  <td>
                    <p className="font-semibold">{c.name}</p>
                    <p className="text-xs opacity-60">{c.subject}</p>
                  </td>
                  <td className="text-xs">{audienceLabel(c)}</td>
                  <td>
                    <span className={`badge badge-sm border ${CAMPAIGN_STATUS[c.status] ?? "badge-ghost"}`}>
                      {c.status}
                    </span>
                  </td>
                  <td className="text-right text-xs">
                    {c.status === "draft" ? "—" : `${c.sent_count} sent · ${c.suppressed_count} held · ${c.failed_count} failed`}
                  </td>
                  <td className="text-xs whitespace-nowrap opacity-70">{formatDateTime(c.created_at)}</td>
                  <td>
                    <div className="flex items-center justify-end gap-1">
                      <button
                        type="button"
                        className="btn btn-ghost btn-xs btn-square"
                        onClick={() => setDrawerId(c.id)}
                        aria-label={`Open ${c.name}`}
                        title="Read the campaign"
                      >
                        <Eye size={13} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {campaigns.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-10 text-center text-sm opacity-60">
                    No campaigns yet — write the first one above.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* -------------------------------------------------- VOUCHERS ---- */}
      <section className="space-y-4">
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="font-display text-2xl font-semibold">Personal vouchers</h2>
          <p className="text-xs opacity-55">
            {overview.voucher_percent_cap != null ? (
              <>
                The 40% margin floor caps any voucher at {overview.voucher_percent_cap}% or{" "}
                {money(overview.voucher_fixed_cap)} — from {overview.costed_products} costed{" "}
                {overview.costed_products === 1 ? "product" : "products"}.
              </>
            ) : (
              <>No costing data yet, so no margin-safe bound exists — the desk refuses rather than guess.</>
            )}
          </p>
        </div>

        <form onSubmit={mint} className="space-y-4 rounded-box glass p-5 shadow-sm ring-1 ring-base-300/60">
          <CustomerPicker
            picked={voucher.picked}
            onPick={(u) => setVoucher((v) => ({ ...v, picked: u }))}
            idPrefix="vch"
          />

          <div className="flex flex-wrap items-end gap-3">
            <div className="form-control">
              <div className="join">
                {[
                  ["percent", "% off"],
                  ["fixed", "€ off"],
                ].map(([value, text]) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setVoucher((v) => ({ ...v, kind: value }))}
                    className={`btn btn-sm join-item ${voucher.kind === value ? "btn-secondary text-secondary-content" : "btn-ghost border border-base-300"}`}
                    aria-pressed={voucher.kind === value}
                  >
                    {text}
                  </button>
                ))}
              </div>
            </div>
            {voucher.kind === "percent" ? (
              <div>
                <label className="label text-xs" htmlFor="vch-percent">Percent</label>
                <input
                  id="vch-percent"
                  type="number"
                  className="input input-bordered input-sm w-24"
                  required
                  min={1}
                  max={overview.voucher_percent_cap ?? 100}
                  step={1}
                  value={voucher.percent}
                  onChange={(e) => setVoucher((v) => ({ ...v, percent: e.target.value }))}
                />
              </div>
            ) : (
              <div>
                <label className="label text-xs" htmlFor="vch-amount">Amount (€)</label>
                <input
                  id="vch-amount"
                  type="number"
                  className="input input-bordered input-sm w-28"
                  required
                  min={0.01}
                  step={0.01}
                  value={voucher.amount}
                  onChange={(e) => setVoucher((v) => ({ ...v, amount: e.target.value }))}
                />
              </div>
            )}
            <div>
              <label className="label text-xs" htmlFor="vch-expiry">Expires (optional)</label>
              <input
                id="vch-expiry"
                type="date"
                className="input input-bordered input-sm"
                value={voucher.expires}
                onChange={(e) => setVoucher((v) => ({ ...v, expires: e.target.value }))}
              />
            </div>
            <div>
              <label className="label text-xs" htmlFor="vch-max">Max uses (optional)</label>
              <input
                id="vch-max"
                type="number"
                className="input input-bordered input-sm w-24"
                min={1}
                step={1}
                value={voucher.max}
                onChange={(e) => setVoucher((v) => ({ ...v, max: e.target.value }))}
              />
            </div>
          </div>
          <div>
            <label className="label text-xs" htmlFor="vch-note">Note (optional)</label>
            <input
              id="vch-note"
              className="input input-bordered input-sm w-full max-w-md"
              maxLength={255}
              placeholder="Why the shop is saying thank you"
              value={voucher.note}
              onChange={(e) => setVoucher((v) => ({ ...v, note: e.target.value }))}
            />
          </div>
          <p className="text-[11px] opacity-50">
            Every voucher is one-per-customer and locked to its account — only its owner can ever
            redeem it, and nobody else can even see it on the board.
          </p>
          <button
            type="submit"
            className="btn btn-primary btn-sm"
            disabled={mintBusy || !voucher.picked}
          >
            <TicketPercent size={14} />
            {mintBusy ? "Minting…" : "Mint the voucher"}
          </button>
        </form>

        <div className="overflow-x-auto rounded-box glass shadow-sm ring-1 ring-base-300/60">
          <table className="table table-sm">
            <thead>
              <tr className="text-xs uppercase opacity-50">
                <th>Code</th>
                <th>For</th>
                <th>Worth</th>
                <th>Expires</th>
                <th className="text-right">Used</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {vouchers.map((v) => (
                <tr key={v.id} className="hover:bg-base-200/50">
                  <td>
                    <span className={`badge badge-sm border font-mono font-bold ${v.is_active ? "" : "opacity-40"}`}>
                      {v.code}
                    </span>
                  </td>
                  <td className="text-sm">{v.restricted_to_name ?? `customer #${v.restricted_to_user_id}`}</td>
                  <td className="text-sm font-semibold">
                    {v.fixed_amount_off != null ? `${money(v.fixed_amount_off)} off` : `${v.percent_off}% off`}
                  </td>
                  <td className="text-xs opacity-70">
                    {v.expires_at ? formatDateTime(v.expires_at) : "no expiry"}
                  </td>
                  <td className="text-right text-sm">
                    {v.redemptions}
                    {v.max_redemptions != null && <span className="opacity-50"> / {v.max_redemptions}</span>}
                  </td>
                  <td>
                    <div className="flex items-center justify-end gap-1">
                      <button
                        type="button"
                        className="btn btn-ghost btn-xs btn-square"
                        onClick={() => copyCode(v.code)}
                        aria-label={`Copy ${v.code}`}
                        title="Copy the code"
                      >
                        <Copy size={13} />
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost btn-xs btn-square"
                        onClick={() => emailVoucher(v)}
                        disabled={sendBusyId === v.id || !v.is_active}
                        aria-label={`Email ${v.code} to ${v.restricted_to_name ?? "its customer"}`}
                        title="Email it — needs recorded consent"
                      >
                        <Send size={13} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {vouchers.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-10 text-center text-sm opacity-60">
                    No personal vouchers yet — mint one above.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* ---------------------------------------------- SUPPRESSION ---- */}
      <section className="space-y-4">
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="font-display text-2xl font-semibold">Suppression list</h2>
          <p className="text-xs opacity-55">
            Nothing marketing is ever mailed to these addresses. Rows leave one way only: a freshly
            recorded consent.
          </p>
        </div>
        <div className="overflow-x-auto rounded-box glass shadow-sm ring-1 ring-base-300/60">
          <table className="table table-sm">
            <thead>
              <tr className="text-xs uppercase opacity-50">
                <th>Email</th>
                <th>Reason</th>
                <th>Note</th>
                <th>Since</th>
              </tr>
            </thead>
            <tbody>
              {suppression.map((s) => (
                <tr key={s.id} className="hover:bg-base-200/50">
                  <td className="flex items-center gap-2 text-sm">
                    <Ban size={13} className="shrink-0 opacity-40" aria-hidden="true" />
                    {s.email}
                  </td>
                  <td><span className="badge badge-sm badge-ghost">{s.reason}</span></td>
                  <td className="text-xs opacity-60">{s.note ?? "—"}</td>
                  <td className="text-xs whitespace-nowrap opacity-70">{formatDateTime(s.created_at)}</td>
                </tr>
              ))}
              {suppression.length === 0 && (
                <tr>
                  <td colSpan={4} className="py-10 text-center text-sm opacity-60">
                    Empty — nobody has asked the shop to stop.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {drawerId !== null && (
        <CampaignDrawer
          campaignId={drawerId}
          onClose={() => setDrawerId(null)}
          onSent={load}
        />
      )}
    </div>
  );
}
