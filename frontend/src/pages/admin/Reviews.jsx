import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { BadgeCheck, EyeOff, Trash2 } from "lucide-react";
import client, { apiError } from "../../api/client";
import { formatDate } from "../../lib/format";
import { useToast } from "../../context/ToastContext";
import ConfirmDialog from "../../components/ConfirmDialog";
import RatingStars from "../../components/RatingStars";
import { Loading } from "../../components/Loading";

const FILTERS = [
  { value: "live", label: "On the wall" },
  { value: "hidden", label: "Taken down" },
  { value: "all", label: "Everything" },
];

/** One review as the desk works it: whose, on what, and whether it shows. */
function ReviewRow({ review, highlighted, focusRef, onModerate, onDelete }) {
  return (
    <article
      className={`rounded-box glass p-5 shadow-sm ring-1 transition ${
        highlighted
          ? "ring-secondary ring-2"
          : "ring-base-300/60"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
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
            {formatDate(review.created_at)}
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
    </article>
  );
}

export default function AdminReviews() {
  const toast = useToast();
  const [params] = useSearchParams();
  const focusId = Number(params.get("focus")) || null;
  const [filter, setFilter] = useState("live");
  const [reviews, setReviews] = useState(null);
  const [pendingDelete, setPendingDelete] = useState(null);
  const [busy, setBusy] = useState(false);
  const focusRef = useRef(null);

  const load = (current = filter) =>
    client.get("/admin/reviews", { params: { status: current } })
      .then(({ data }) => {
        setReviews(data);
        return data;
      });

  useEffect(() => {
    let stale = false;
    load().then((data) => {
      // A bell ping can point at a taken-down review. If the focused one
      // isn't on this shelf, widen to Everything so the highlight has a
      // card to land on instead of quietly matching nothing.
      if (stale || !focusId || filter === "all") return;
      if (!data.some((r) => r.id === focusId)) setFilter("all");
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
      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            onClick={() => setFilter(f.value)}
            className={`btn btn-sm ${filter === f.value ? "btn-secondary text-secondary-content" : "btn-ghost border border-base-300"}`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {reviews.length === 0 && (
        <p className="rounded-box border border-dashed border-base-300 p-8 text-center text-sm opacity-60">
          {filter === "hidden"
            ? "Nothing taken down — the wall is all yours."
            : "No reviews here yet."}
        </p>
      )}

      <div className="space-y-3">
        {reviews.map((r) => (
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
