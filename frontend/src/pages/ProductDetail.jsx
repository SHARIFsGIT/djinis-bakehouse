import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { BadgeCheck, Bell, Heart, Share2, Star, Truck, Wheat } from "lucide-react";
import client, { apiError } from "../api/client";
import { formatDate, shopConfig } from "../lib/format";
import { useAuth } from "../context/AuthContext";
import { useCart } from "../context/CartContext";
import { useFavorites } from "../context/FavoritesContext";
import { useToast } from "../context/ToastContext";
import PhotoTile from "../components/PhotoTile";
import PriceTag from "../components/PriceTag";
import RatingStars from "../components/RatingStars";
import QuantityInput from "../components/QuantityInput";
import ProductCard from "../components/ProductCard";
import { Loading } from "../components/Loading";

const DEFAULT_TITLE = "Djini's Bakehouse — Fresh from the oven";

/** Set (or replace) a <meta name=…> tag — crawlers and share cards read
    these after the SPA paints, so each product page carries its own. */
function setMetaTag(name, content) {
  let tag = document.head.querySelector(`meta[name="${name}"]`);
  if (!tag) {
    tag = document.createElement("meta");
    tag.setAttribute("name", name);
    document.head.appendChild(tag);
  }
  tag.setAttribute("content", content);
}

/** The product page's own <title>, description and Product structured
    data — the crawl files point spiders here, so the page must say what
    it is. Cleared on unmount so the next page starts clean. */
function useProductHead(product) {
  useEffect(() => {
    if (!product) return undefined;
    document.title = `${product.name} — Djini's Bakehouse`;
    const description = (product.description || DEFAULT_TITLE).slice(0, 160);
    setMetaTag("description", description);

    const script = document.createElement("script");
    script.type = "application/ld+json";
    script.id = "product-structured-data";
    script.textContent = JSON.stringify({
      "@context": "https://schema.org",
      "@type": "Product",
      name: product.name,
      sku: String(product.id),
      description,
      ...(product.image_url
        ? { image: [new URL(product.image_url, window.location.origin).href] }
        : {}),
      offers: {
        "@type": "Offer",
        url: window.location.href,
        priceCurrency: shopConfig.currency || "EUR",
        price: product.current_price.toFixed(2),
        availability:
          product.stock > 0
            ? "https://schema.org/InStock"
            : "https://schema.org/OutOfStock",
      },
      ...(product.review_count > 0
        ? {
            aggregateRating: {
              "@type": "AggregateRating",
              ratingValue: product.average_rating,
              reviewCount: product.review_count,
            },
          }
        : {}),
    });
    document.head.appendChild(script);

    return () => {
      script.remove();
      document.title = DEFAULT_TITLE;
    };
  }, [product]);
}

/** Interactive 1–5 star picker for the review form. */
function StarPicker({ value, onChange }) {
  const [hover, setHover] = useState(0);
  const shown = hover || value;
  return (
    <div
      className="flex gap-1 text-3xl text-warning"
      onMouseLeave={() => setHover(0)}
    >
      {[1, 2, 3, 4, 5].map((star) => (
        <button
          key={star}
          type="button"
          onClick={() => onChange(star)}
          onMouseEnter={() => setHover(star)}
          className="transition hover:scale-110"
          aria-label={`${star} star${star > 1 ? "s" : ""}`}
          aria-pressed={value === star}
        >
          {star <= shown ? (
            <Star size={18} className="fill-current" />
          ) : (
            <Star size={18} className="opacity-40" />
          )}
        </button>
      ))}
    </div>
  );
}

/** The average rating, shown as stars the shopper can also *use*: hover to
 *  preview a rating, tap to make it yours — the review form below fills in
 *  and slides into view. Guests are walked to the login door instead. */
function InteractiveRating({ average, count, onPick, canReview }) {
  const [hover, setHover] = useState(0);
  const shown = hover || Math.round(average);
  const stars = [1, 2, 3, 4, 5].map((star) => (
    <span
      key={star}
      role="button"
      tabIndex={0}
      onMouseEnter={() => canReview && setHover(star)}
      onClick={() => canReview && onPick(star)}
      onKeyDown={(e) => {
        if (canReview && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          onPick(star);
        }
      }}
      className={`text-warning ${canReview ? "cursor-pointer transition hover:scale-110" : ""}`}
      aria-label={`${star} star${star > 1 ? "s" : ""}`}
    >
      {star <= shown ? (
        <Star size={17} className="fill-current" />
      ) : (
        <Star size={17} className="opacity-40" />
      )}
    </span>
  ));

  const caption = hover
    ? `Rate it ${hover} star${hover > 1 ? "s" : ""}`
    : count > 0
      ? `${average.toFixed(1)} · ${count} review${count === 1 ? "" : "s"}`
      : "No reviews yet — tap to be the first";

  return (
    <div className="flex flex-wrap items-center gap-2.5">
      <div
        className="flex items-center gap-0.5"
        onMouseLeave={() => setHover(0)}
        title={canReview ? "Tap a star to rate this bake" : undefined}
      >
        {stars}
      </div>
      <p className="text-xs opacity-60">{caption}</p>
    </div>
  );
}

export default function ProductDetail() {
  const { slug } = useParams();
  const { user } = useAuth();
  const { add } = useCart();
  const { isSaved, toggle } = useFavorites();
  const toast = useToast();

  const [product, setProduct] = useState(null);
  const [error, setError] = useState(null);
  const [quantity, setQuantity] = useState(1);
  const [reviewRating, setReviewRating] = useState(5);
  const [reviewComment, setReviewComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [restockEmail, setRestockEmail] = useState("");
  const [restockDone, setRestockDone] = useState(false);
  const [restockSending, setRestockSending] = useState(false);

  useProductHead(product);

  const loadProduct = () =>
    client
      .get(`/products/${slug}`)
      .then(({ data }) => setProduct(data))
      .catch((e) => setError(apiError(e, "Couldn't find that bake")));

  useEffect(() => {
    setProduct(null);
    setError(null);
    setQuantity(1);
    setRestockEmail("");
    setRestockDone(false);
    loadProduct();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  // A returning reviewer's stars wait where they left them — the pickers
  // (the one under the title and the form's own) open on their rating, not
  // on a default five.
  useEffect(() => {
    if (!product || !user) return;
    const own = product.reviews.find((r) => r.author_id === user.id);
    if (own) setReviewRating(own.rating);
  }, [product, user]);

  if (error) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-16 text-center">
        <h1 className="font-display mt-4 text-3xl font-semibold">
          We couldn&rsquo;t find that one
        </h1>
        <p className="mt-2 opacity-70">{error}</p>
        <Link to="/products" className="btn btn-primary btn mt-6">
          All bakes
        </Link>
      </div>
    );
  }
  if (!product) return <Loading label="Pulling it from the shelf…" />;

  const soldOut = product.stock <= 0;
  const lowStock = !soldOut && product.stock <= 5;
  // The wall shows only live reviews; a taken-down one stays with its
  // author, inside their own form below, flagged.
  const wall = product.reviews.filter((r) => r.is_approved);
  const mine = user
    ? product.reviews.find((r) => r.author_id === user.id)
    : null;
  const saved = isSaved(product.id);

  /** A star tapped on the product itself: set the rating and bring the
   *  review form into view, ready for the words. */
  const pickRating = (stars) => {
    setReviewRating(stars);
    document.getElementById("review-form")?.scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
  };

  const shareLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      toast.success("Link copied — pass it on");
    } catch {
      toast.info(window.location.href);
    }
  };

  const distribution = [5, 4, 3, 2, 1].map((stars) => ({
    stars,
    count: wall.filter((r) => r.rating === stars).length,
  }));

  const submitReview = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      await client.post(`/products/${product.id}/reviews`, {
        rating: reviewRating,
        comment: reviewComment.trim() || null,
      });
      toast.success(mine ? "Your review was updated" : "Thanks for the review");
      setReviewComment("");
      await loadProduct();
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setSubmitting(false);
    }
  };

  const deleteReview = async (reviewId) => {
    try {
      await client.delete(`/products/${product.id}/reviews/${reviewId}`);
      toast.info("Review removed");
      await loadProduct();
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  const askForRestock = async (e) => {
    e.preventDefault();
    setRestockSending(true);
    try {
      await client.post(`/products/${product.id}/restock-alert`, {
        email: restockEmail.trim(),
      });
      setRestockDone(true);
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setRestockSending(false);
    }
  };

  return (
    <div className="mx-auto max-w-7xl px-4 py-10">
      {/* Breadcrumb */}
      <nav className="breadcrumbs mb-6 text-sm" aria-label="Breadcrumb">
        <ul>
          <li>
            <Link to="/products">Shop</Link>
          </li>
          {product.category_slug && (
            <li>
              <Link to={`/products?category=${product.category_slug}`}>
                {product.category_name}
              </Link>
            </li>
          )}
          <li className="opacity-60">{product.name}</li>
        </ul>
      </nav>

      <div className="grid grid-cols-1 gap-10 lg:grid-cols-2">
        {/* Photo */}
        <div className="rise-in relative">
          <div className="overflow-hidden rounded-box shadow-lg ring-1 ring-base-300/60">
            <PhotoTile
              src={product.image_url}
              name={product.name}
              className="aspect-[3/4] w-full"
            />
          </div>
          {product.is_on_sale && (
            <span className="badge badge-secondary badge-lg absolute top-4 left-4 -rotate-6 border-dashed border-secondary-content/40 px-3 py-3 font-display text-base font-bold shadow">
              −{product.discount_percentage}% today
            </span>
          )}
        </div>

        {/* Info */}
        <div className="rise-in">
          <div className="flex items-center gap-2">
            {product.category_name && (
              <span className="badge badge-outline">
                {product.category_name}
              </span>
            )}
            {product.is_featured && (
              <span className="badge badge-primary">House favorite</span>
            )}
          </div>

          <h1 className="font-display mt-3 text-4xl leading-tight font-semibold md:text-5xl">
            {product.name}
          </h1>

          {/* The average, wearable: hover to preview a rating, tap to make
              it yours — the form below fills in. */}
          <div className="mt-3">
            {user ? (
              <InteractiveRating
                average={product.average_rating}
                count={product.review_count}
                onPick={pickRating}
                canReview
              />
            ) : (
              <Link
                to="/login"
                state={{ from: window.location.pathname }}
                className="inline-block"
                title="Log in to rate this bake"
              >
                <InteractiveRating
                  average={product.average_rating}
                  count={product.review_count}
                  onPick={() => {}}
                />
              </Link>
            )}
          </div>

          <div className="mt-5">
            <PriceTag product={product} large />
          </div>

          <p className="mt-5 leading-relaxed opacity-80">
            {product.description}
          </p>

          <div className="mt-5 flex flex-wrap gap-2 text-sm">
            {/* Prep time and the allergen summary are gone from the badge
                row — the allergen detail lives in its own fold below, where
                the whole list fits. What stays is what a shopper scanning
                the row can act on. */}
            <span className="badge badge-ghost gap-1.5 py-2.5">
              <Wheat size={13} className="text-accent" />
              Baked fresh every morning
            </span>
            {soldOut ? (
              <span className="badge badge-error badge-outline">
                Sold out today
              </span>
            ) : (
              <span
                className={`badge ${lowStock ? "badge-warning" : "badge-success"} badge-outline`}
              >
                {lowStock
                  ? `Only ${product.stock} left`
                  : `${product.stock} in the case`}
              </span>
            )}
          </div>

          <div className="dash-divider mt-6 flex flex-wrap items-center gap-4 pt-6">
            <QuantityInput
              value={quantity}
              onChange={setQuantity}
              max={Math.max(product.stock, 1)}
            />
            <button
              type="button"
              className="btn btn-primary btn-lg flex-1 sm:flex-none"
              disabled={soldOut}
              onClick={() => {
                add(product, quantity);
                toast.success(`${quantity} × ${product.name} in your basket`);
              }}
            >
              {soldOut ? "Sold out" : "Add to basket"}
            </button>
            <button
              type="button"
              className={`btn btn-sm btn-square ${saved ? "btn-secondary text-secondary-content" : "btn-ghost border border-base-300"}`}
              onClick={() => toggle(product)}
              aria-label={saved ? "Remove from saved bakes" : "Save for later"}
              aria-pressed={saved}
              title={saved ? "Remove from saved bakes" : "Save for later"}
            >
              {saved ? (
                <Heart size={18} className="fill-current" />
              ) : (
                <Heart size={18} />
              )}
            </button>
            <button
              type="button"
              className="btn btn-sm btn-square btn-ghost border border-base-300"
              onClick={shareLink}
              aria-label="Copy link to this bake"
              title="Copy link"
            >
              <Share2 size={18} />
            </button>
          </div>

          {soldOut &&
            (restockDone ? (
              <p className="mt-4 flex items-center gap-2 rounded-box border border-dashed border-secondary/40 bg-base-200/60 px-4 py-3 text-sm">
                <Bell size={16} className="shrink-0 text-secondary" />
                You&rsquo;re on the list — one email, the moment it&rsquo;s
                back.
              </p>
            ) : (
              <form
                onSubmit={askForRestock}
                className="mt-4 rounded-box border border-dashed border-base-300 bg-base-200/60 p-4"
              >
                <p className="flex items-center gap-2 text-sm font-semibold">
                  <Bell size={16} className="shrink-0 text-secondary" />
                  Want one when it&rsquo;s back?
                </p>
                <p className="mt-1 text-xs opacity-70">
                  Leave your email — we write once, the day the shelf refills.
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <input
                    type="email"
                    required
                    value={restockEmail}
                    onChange={(e) => setRestockEmail(e.target.value)}
                    placeholder="you@example.com"
                    className="input input-bordered input-sm min-w-0 flex-1"
                    aria-label="Email me when this bake is back"
                  />
                  <button
                    type="submit"
                    className="btn btn-secondary btn-sm text-secondary-content"
                    disabled={restockSending}
                  >
                    {restockSending ? "Saving…" : "Tell me when it's back"}
                  </button>
                </div>
              </form>
            ))}

          <p className="mt-4 flex items-center gap-2 text-sm opacity-70">
            <Truck size={16} className="shrink-0 text-secondary" />
            Order by 2 p.m. — it&rsquo;s on the evening delivery run, or ready
            at the counter from 7 a.m.
          </p>

          {(product.ingredients || product.allergens) && (
            <div className="mt-6 space-y-3">
              {product.ingredients && (
                <div className="collapse collapse-arrow bg-base-200/60">
                  <input type="checkbox" defaultChecked />
                  <div className="collapse-title text-sm font-semibold">
                    Ingredients
                  </div>
                  <div className="collapse-content text-sm opacity-80">
                    {product.ingredients}
                  </div>
                </div>
              )}
              {product.allergens && (
                <div className="collapse collapse-arrow bg-base-200/60">
                  <input type="checkbox" />
                  <div className="collapse-title text-sm font-semibold">
                    Allergens
                  </div>
                  <div className="collapse-content text-sm opacity-80">
                    Contains: {product.allergens}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Reviews */}
      <section className="dash-divider mt-16 pt-10">
        <div className="grid grid-cols-1 gap-10 lg:grid-cols-[1fr_1.4fr]">
          <div>
            <h2 className="font-display text-2xl font-semibold">
              What the neighborhood says
            </h2>
            <div className="mt-4 flex items-end gap-3">
              <p className="font-display text-6xl font-bold text-secondary">
                {product.review_count ? product.average_rating.toFixed(1) : "—"}
              </p>
              <div className="pb-2">
                <RatingStars rating={product.average_rating} />
                <p className="text-xs opacity-60">
                  {product.review_count} reviews
                </p>
              </div>
            </div>

            {product.review_count > 0 && (
              <ul className="mt-4 space-y-1.5">
                {distribution.map(({ stars, count }) => (
                  <li key={stars} className="flex items-center gap-2 text-xs">
                    <span className="flex w-8 items-center gap-0.5 font-semibold">
                      {stars}
                      <Star size={12} className="fill-current text-warning" />
                    </span>
                    <div className="h-2 flex-1 overflow-hidden rounded-full bg-base-200">
                      <div
                        className="brand-gradient h-full rounded-full"
                        style={{
                          width: `${(count / product.review_count) * 100}%`,
                        }}
                      />
                    </div>
                    <span className="w-6 text-right opacity-60">{count}</span>
                  </li>
                ))}
              </ul>
            )}

            {user ? (
              <form
                id="review-form"
                onSubmit={submitReview}
                className="mt-6 rounded-box bg-base-200/60 p-5"
              >
                <p className="mb-2 font-semibold">
                  {mine ? "Update your review" : "Leave a review"}
                </p>
                {mine && !mine.is_approved && (
                  <p className="mb-2 rounded-lg bg-base-200 px-3 py-2 text-xs opacity-70">
                    The shop has taken this review off the public wall. You can
                    still read and edit your copy.
                  </p>
                )}
                <StarPicker value={reviewRating} onChange={setReviewRating} />
                <textarea
                  className="textarea textarea-bordered mt-3 w-full"
                  rows={3}
                  maxLength={2000}
                  placeholder="How was it? Crust? Crumb? Worth waking up early for?"
                  value={reviewComment}
                  onChange={(e) => setReviewComment(e.target.value)}
                />
                <div className="mt-3 flex items-center gap-3">
                  <button
                    type="submit"
                    className="btn btn-secondary btn-sm text-secondary-content"
                    disabled={submitting}
                  >
                    {submitting
                      ? "Sending…"
                      : mine
                        ? "Update review"
                        : "Post review"}
                  </button>
                  {mine && (
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm text-error"
                      onClick={() => deleteReview(mine.id)}
                    >
                      Delete mine
                    </button>
                  )}
                </div>
              </form>
            ) : (
              <div className="mt-6 rounded-box bg-base-200/60 p-5 text-sm">
                <p className="opacity-80">Tried it? Share your crumb report.</p>
                <Link to="/login" className="btn btn-primary btn-sm btn mt-3">
                  Log in to review
                </Link>
              </div>
            )}
          </div>

          <div className="space-y-4">
            {wall.length === 0 && (
              <p className="rounded-box border border-dashed border-base-300 p-6 text-center text-sm opacity-60">
                No reviews yet — be the first to write one
              </p>
            )}
            {wall.map((r) => (
              <article
                key={r.id}
                className="rounded-box glass p-4 shadow-sm ring-1 ring-base-300/60"
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="flex items-center gap-2 font-semibold">
                    {r.author_name ?? "Anonymous baker"}
                    {r.verified_purchase && (
                      <span className="badge badge-success badge-sm gap-1 py-2 text-[11px] text-success-content">
                        <BadgeCheck size={12} /> Verified purchase
                      </span>
                    )}
                  </p>
                  <p className="text-xs opacity-50">
                    {formatDate(r.created_at)}
                  </p>
                </div>
                <div className="mt-1">
                  <RatingStars rating={r.rating} />
                </div>
                {r.comment && (
                  <p className="mt-2 text-sm leading-relaxed opacity-80">
                    {r.comment}
                  </p>
                )}
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* Related */}
      {product.related.length > 0 && (
        <section className="mt-16">
          <h2 className="font-display mb-6 text-2xl font-semibold">
            You might also like
          </h2>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            {product.related.map((p) => (
              <ProductCard key={p.id} product={p} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
