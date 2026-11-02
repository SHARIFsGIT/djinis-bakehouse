import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { BadgeCheck, EyeOff, Search, Trash2 } from "lucide-react";
import client, { apiError } from "../../api/client";
import { formatDateTime, productInitial, stampTime } from "../../lib/format";
import { useToast } from "../../context/ToastContext";
import ConfirmDialog from "../../components/ConfirmDialog";
import RatingStars from "../../components/RatingStars";
import { Loading } from "../../components/Loading";

const FILTERS = [
  { value: "live", label: "On the wall", count: "live" },
  { value: "hidden", label: "Taken down", count: "hidden" },
  { value: "all", label: "Everything", count: "all" },
];

/** The bake's photo — or its initial on a quiet tile when there isn't one. */
function ProductThumb({ name, image }) {
  if (image) {
    return (
      <img
        src={image}
        alt=""
        className="h-11 w-11 shrink-0 rounded-lg object-cover ring-1 ring-base-300/70"
      />
    );
  }
  return (
    <span className="brand-gradient flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-sm font-bold text-secondary-content">
      {productInitial(name)}
    </span>
  );
}

/** One review as the desk works it: whose, on what, and whether it shows. */
function ReviewRow({ review, highlighted, focusRef, onModerate, onDelete }) {
  return (
    <article
      className={`rounded-box glass p-4 shadow-sm ring-1 transition sm:p-5 ${
        highlighted
          ? "ring-secondary ring-2"
          : "ring-base-300/60"
      }`}
    >
      <div className="flex items-start gap-3 sm:gap-4">
        <ProductThumb name={review.product_name} image={review.product_image} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
            <div className="min-w-0">
              <p className="flex flex-wrap items-center gap-2">
                <Link
                  to={`/products/${review.product_slug}`}
                  className="font-display font-semibold hover:text-secondary"
                >
                  {review.product_name}
                </Link>
                <RatingStars rating={review.rating} />
                {review.verified_purchase && (
                  <span className="badge badge-success badge-sm gap-1 text-success-content">
                    <BadgeCheck size={12} aria-hidden="true" /> Verified purchase
                  </span>
                )}
                {!review.is_approved && (
                  <span className="badge badge-ghost badge-sm gap-1 opacity-70">
                    <EyeOff size={12} aria-hidden="true" /> Taken down
                  </span>
                )}
              </p>
              <p className="mt-0.5 text-xs opacity-60">
                {review.author_name ?? "Someone"} · {review.author_email} ·{" "}
                <span title={formatDateTime(review.created_at)}>
                  {stampTime(review.created_at)}
                </span>
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <button
                type="button"
                ref={focusRef}
                className={`btn btn-xs ${review.is_approved ? "btn-ghost border border-base-300" : "btn-primary"}`}
                onClick={() => onModerate(review)}
              >
                {review.is_approved ? "Take down" : "Put back"}
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-xs btn-square text-error"
                onClick={() => onDelete(review)}
                aria-label={`Delete review by ${review.author_name}`}
              >
                <Trash2 size={14} />
              </button>
            </div>
          </div>
          {review.comment && (
            <p className="mt-3 text-sm leading-relaxed opacity-80">{review.comment}</p>
          )}
        </div>
      </div>
    </article>
  );
}

export default function AdminReviews() {
  const toast = useToast();
  const [params] = useSearchParams();
  const focusId = Number(params.get("focus")) || null;
  const [filter, setFilter] = useState("live");
  const [reviews, setReviews] = useState(null);
  const [counts, setCounts] = useState({ live: 0, hidden: 0, all: 0, live_average: null });
  const [query, setQuery] = useState("");
  const [pendingDelete, setPendingDelete] = useState(null);
  const [busy, setBusy] = useState(false);
  const focusRef = useRef(null);

  const load = (current = filter) =>
    client.get("/admin/reviews", { params: { status: current } }).then(({ data }) => {
      setReviews(data.items);
      setCounts(data.counts);
      return data;
    });

  useEffect(() => {
    let stale = false;
    load().then((data) => {
      // A bell ping can point at a taken-down review. If the focused one
      // isn't on this shelf, widen to Everything so the highlight has a
      // card to land on instead of quietly matching nothing.
      if (stale || !focusId || filter === "all") return;
      if (!data.items.some((r) => r.id === focusId)) setFilter("all");
    });
    return () => {
      stale = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter, focusId]);

  // A bell click lands here with ?focus= — make that review easy to find
  useEffect(() => {
    if (reviews !== null && focusId && focusRef.current) {
      focusRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [reviews, focusId]);

  // The desk's own eye: find a review by the bake, the writer or a word
  // in the note — across whichever shelf is open.
  const needle = query.trim().toLowerCase();
  const visible = useMemo(
    () =>
      needle
        ? reviews.filter((r) =>
            [r.product_name, r.author_name, r.author_email, r.comment]
              .filter(Boolean)
              .some((field) => field.toLowerCase().includes(needle)),
          )
        : reviews,
    [reviews, needle],
  );

  const moderate = async (review) => {
    try {
      await client.put(`/admin/reviews/${review.id}`, {
        is_approved: !review.is_approved,
      });
      toast.success(review.is_approved ? "Taken off the wall" : "Back on the wall");
      await load();
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await client.delete(`/admin/reviews/${pendingDelete.id}`);
      toast.success("Review deleted");
      setPendingDelete(null);
      await load();
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setBusy(false);
    }
  };

  if (reviews === null) return <Loading label="Reading the reviews…" />;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              type="button"
              onClick={() => setFilter(f.value)}
              className={`btn btn-sm gap-2 ${filter === f.value ? "btn-secondary text-secondary-content" : "btn-ghost border border-base-300"}`}
            >
              {f.label}
              <span
                className={`badge badge-sm border-0 ${filter === f.value ? "bg-secondary-content/20 text-secondary-content" : "bg-base-200 text-base-content/70"}`}
              >
                {counts[f.count]}
              </span>
            </button>
          ))}
        </div>
        <label className="input input-bordered input-sm flex w-full max-w-56 items-center gap-2 bg-base-100 sm:w-56">
          <Search size={15} className="opacity-50" />
          <input
            type="search"
            className="grow"
            placeholder="Search reviews…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search reviews"
          />
        </label>
      </div>

      {counts.live_average !== null && (
        <p className="text-sm opacity-70">
          The wall averages{" "}
          <span className="font-semibold text-warning">{counts.live_average} of 5</span>{" "}
          across {counts.live} live review{counts.live === 1 ? "" : "s"}.
        </p>
      )}

      {visible.length === 0 && (
        <p className="rounded-box border border-dashed border-base-300 p-8 text-center text-sm opacity-60">
          {needle
            ? `Nothing on this shelf matches “${query.trim()}”.`
            : filter === "hidden"
              ? "Nothing taken down — the wall is all yours."
              : "No reviews here yet."}
        </p>
      )}

      <div className="space-y-3">
        {visible.map((r) => (
          <ReviewRow
            key={r.id}
            review={r}
            highlighted={r.id === focusId}
            focusRef={r.id === focusId ? focusRef : null}
            onModerate={moderate}
            onDelete={setPendingDelete}
          />
        ))}
      </div>

      {pendingDelete && (
        <ConfirmDialog
          title="Delete this review?"
          confirmLabel="Yes, delete"
          busy={busy}
          onConfirm={remove}
          onCancel={() => setPendingDelete(null)}
        >
          By {pendingDelete.author_name} on {pendingDelete.product_name}. This
          one can't be undone — “Take down” is the reversible tool.
        </ConfirmDialog>
      )}
    </div>
  );
}
