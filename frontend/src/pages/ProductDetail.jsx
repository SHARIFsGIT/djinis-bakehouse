import { useEffect, useId, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router";
import { BadgeCheck, Bell, Heart, Share2, Star, Truck, Wheat } from "lucide-react";
import client, { apiError } from "../api/client";
import { formatDate, MAX_LINE_QUANTITY, shopConfig } from "../lib/format";
import { rememberBake } from "../lib/recent";
import { DEFAULT_TITLE, SHOP_NAME } from "../lib/titles";
import { useAuth } from "../context/AuthContext";
import { useCart } from "../context/CartContext";
import { useFavorites } from "../context/FavoritesContext";
import { useToast } from "../context/ToastContext";
import PhotoTile from "../components/PhotoTile";
import PriceTag from "../components/PriceTag";
import RatingStars from "../components/RatingStars";
import QuantityInput from "../components/QuantityInput";
import ProductCard from "../components/ProductCard";
import DockBar from "../components/DockBar";
import Lightbox from "../components/Lightbox";
import RecentlyViewed from "../components/RecentlyViewed";
import useDock from "../hooks/useDock";

/** The page's own outline while the bake is on its way: the photo's frame
    and the lines of text hold their places, so nothing jumps when the real
    thing lands (a bare spinner gave the page nothing to stand on). */
function ProductSkeleton() {
  return (
    <div className="mx-auto max-w-7xl px-4 py-10" aria-busy="true" aria-label="Pulling it from the shelf…">
      <div className="mb-6 h-4 w-48 animate-pulse rounded bg-base-200" />
      <div className="grid grid-cols-1 gap-10 lg:grid-cols-2">
        <div className="aspect-square w-full animate-pulse rounded-box bg-base-200 sm:aspect-[4/5] lg:aspect-[3/4]" />
        <div className="space-y-4">
          <div className="h-5 w-24 animate-pulse rounded-full bg-base-200" />
          <div className="h-11 w-3/4 animate-pulse rounded bg-base-200" />
          <div className="h-4 w-40 animate-pulse rounded bg-base-200" />
          <div className="h-9 w-28 animate-pulse rounded bg-base-200" />
          <div className="h-4 w-full animate-pulse rounded bg-base-200" />
          <div className="h-4 w-5/6 animate-pulse rounded bg-base-200" />
          <div className="h-12 w-full animate-pulse rounded-box bg-base-200 sm:w-72" />
        </div>
      </div>
    </div>
  );
}

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
    document.title = `${product.name} — ${SHOP_NAME}`;
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

/** Ingredients and Allergens: a heading whose button shows or hides the text
 *  beneath it, so a screen reader meets "Allergens, heading" and "button,
 *  collapsed" where it used to find an unnamed checkbox. The look is
 *  daisyUI's collapse, opened by state instead of a hidden input. */
function InfoSection({ title, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <div
      className={`collapse collapse-arrow bg-base-200/60 has-focus-visible:outline-2 has-focus-visible:outline-offset-2 ${
        open ? "collapse-open" : ""
      }`}
    >
      <h2 className="collapse-title p-0 text-sm font-semibold">
        <button
          type="button"
          id={`${id}-button`}
          aria-expanded={open}
          aria-controls={`${id}-panel`}
          onClick={() => setOpen((o) => !o)}
          className="w-full cursor-pointer p-4 pe-12 text-left outline-none"
        >
          {title}
        </button>
      </h2>
      <div
        id={`${id}-panel`}
        role="region"
        aria-labelledby={`${id}-button`}
        className="collapse-content text-sm opacity-80"
      >
        {children}
      </div>
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
      <p className="text-xs opacity-70">{caption}</p>
    </div>
  );
}

export default function ProductDetail() {
  const { slug } = useParams();
  const location = useLocation();
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
  const [zoomed, setZoomed] = useState(false);
  // The main "Add to basket" button. While it is off screen, a phone gets
  // the same button docked to the bottom edge.
  const buyButton = useRef(null);
  // Not for a bake that is sold out: a docked "Sold out" offers nothing.
  const docked = useDock(buyButton, { key: product?.id, enabled: (product?.stock ?? 0) > 0 });

  useProductHead(product);

  // Opening a bake's page puts it on this browser's "recently viewed" list.
  useEffect(() => {
    if (product) rememberBake(product);
  }, [product?.slug]); // eslint-disable-line react-hooks/exhaustive-deps

  // The slug this page is showing *now*. An answer for a bake the shopper
  // has already clicked away from (a slow connection, a quick tap on a
  // related bake) must never be painted under the new address.
  const showing = useRef(slug);
  showing.current = slug;

  const loadProduct = () => {
    const asked = slug;
    return client
      .get(`/products/${asked}`)
      .then(({ data }) => {
        if (showing.current === asked) setProduct(data);
      })
      .catch((e) => {
        if (showing.current === asked) setError(apiError(e, "Couldn't find that bake"));
      });
  };

  useEffect(() => {
    setProduct(null);
    setError(null);
    setQuantity(1);
    setRestockEmail(user?.email ?? "");
    setRestockDone(false);
    setReviewRating(5);
    setReviewComment("");
    loadProduct();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  // A returning reviewer's own review waits where they left it — stars and
  // words both. (The form used to open on an empty box, and "update" with
  // only the stars changed quietly erased what they had written.)
  const ownReview = user ? product?.reviews.find((r) => r.author_id === user.id) : null;
  useEffect(() => {
    if (!ownReview) return;
    setReviewRating(ownReview.rating);
    setReviewComment(ownReview.comment ?? "");
    // Keyed on the review itself: a reload of the same review (after
    // saving) must not overwrite what is being typed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ownReview?.id, ownReview?.rating, ownReview?.comment]);

  // The session can settle after the page does (a reload restores it with
  // a request of its own) — offer the account's address then, unless the
  // shopper has already typed one.
  useEffect(() => {
    if (user?.email) setRestockEmail((typed) => typed || user.email);
  }, [user?.email]);

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
  if (!product) return <ProductSkeleton />;

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

  const addToBasket = () => {
    add(product, quantity);
    toast.success(`${quantity} × ${product.name} in your basket`, {
      action: { label: "View basket", to: "/cart" },
    });
  };

  const shareLink = async () => {
    const url = window.location.href;
    // A phone has its own share sheet (messages, mail, the neighbour's
    // chat) — use it there; copy the link everywhere else. A desktop
    // browser may offer a share dialog too, but at a desk "copied" is the
    // quicker answer, so the sheet is kept for touch screens.
    if (typeof navigator.share === "function" && window.matchMedia?.("(pointer: coarse)").matches) {
      try {
        await navigator.share({ title: product.name, url });
      } catch {
        // The sheet was closed without sharing — nothing to say about that.
      }
      return;
    }
    try {
      await navigator.clipboard.writeText(url);
      toast.success("Link copied — pass it on");
    } catch {
      toast.info(url);
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
      // The form goes back to a blank "leave a review", not the old words.
      setReviewRating(5);
      setReviewComment("");
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
          {/* The page itself: the one crumb that may be cut short (with an
              ellipsis) when the trail is longer than a phone is wide. */}
          <li className="min-w-0 opacity-70" aria-current="page">
            <span className="truncate">{product.name}</span>
          </li>
        </ul>
      </nav>

      <div className="grid grid-cols-1 gap-10 lg:grid-cols-2">
        {/* Photo */}
        <div className="rise-in relative">
          {/* Square on a phone, so the name and the price are on the first
              screen with it; the tall portrait from a tablet up. A tap opens
              the photo large. */}
          {product.image_url ? (
            <button
              type="button"
              onClick={() => setZoomed(true)}
              className="block w-full cursor-zoom-in overflow-hidden rounded-box shadow-lg ring-1 ring-base-300/60"
              aria-label={`Enlarge the photo of ${product.name}`}
            >
              <PhotoTile
                src={product.image_url}
                name={product.name}
                eager
                className="aspect-square w-full sm:aspect-[4/5] lg:aspect-[3/4]"
              />
            </button>
          ) : (
            <div className="overflow-hidden rounded-box shadow-lg ring-1 ring-base-300/60">
              <PhotoTile name={product.name} className="aspect-square w-full sm:aspect-[4/5] lg:aspect-[3/4]" />
            </div>
          )}
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
                state={{ from: location.pathname }}
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

          {/* One line from a tablet up. On a phone the stepper shares its
              row with save and share, and the button takes a full row of
              its own — it used to be squeezed until its label broke in two. */}
          <div className="dash-divider mt-6 flex flex-wrap items-center gap-3 pt-6 sm:gap-4">
            <QuantityInput
              value={quantity}
              onChange={setQuantity}
              max={Math.min(Math.max(product.stock, 1), MAX_LINE_QUANTITY)}
            />
            <button
              ref={buyButton}
              type="button"
              className="btn btn-primary btn-lg order-last w-full whitespace-nowrap sm:order-none sm:w-auto"
              disabled={soldOut}
              onClick={addToBasket}
            >
              {soldOut ? "Sold out" : "Add to basket"}
            </button>
            <div className="ml-auto flex items-center gap-2 sm:ml-0">
              <button
                type="button"
                className={`btn btn-square ${saved ? "btn-secondary text-secondary-content" : "btn-ghost border border-base-300"}`}
                onClick={() => toggle(product)}
                aria-label={saved ? "Remove from saved bakes" : "Save for later"}
                aria-pressed={saved}
                title={saved ? "Remove from saved bakes" : "Save for later"}
              >
                <Heart key={String(saved)} size={18} className={saved ? "animate-pop fill-current" : ""} aria-hidden="true" />
              </button>
              <button
                type="button"
                className="btn btn-square btn-ghost border border-base-300"
                onClick={shareLink}
                aria-label="Share this bake"
                title="Share"
              >
                <Share2 size={18} aria-hidden="true" />
              </button>
            </div>
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
                <InfoSection title="Ingredients" defaultOpen>
                  {product.ingredients}
                </InfoSection>
              )}
              {product.allergens && (
                <InfoSection title="Allergens">
                  Contains: {product.allergens}
                </InfoSection>
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
              {/* The big figure only once there is one — a lone dash in
                  its place read as a stray line. */}
              {product.review_count > 0 && (
                <p className="font-display text-6xl font-bold text-secondary">
                  {product.average_rating.toFixed(1)}
                </p>
              )}
              <div className={product.review_count > 0 ? "pb-2" : ""}>
                <RatingStars rating={product.average_rating} />
                <p className="text-xs opacity-70">
                  {product.review_count} {product.review_count === 1 ? "review" : "reviews"}
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
                    <span className="w-6 text-right opacity-70">{count}</span>
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
                <Link
                  to="/login"
                  state={{ from: location.pathname }}
                  className="btn btn-primary btn-sm btn mt-3"
                >
                  Log in to review
                </Link>
              </div>
            )}
          </div>

          <div className="space-y-4">
            {wall.length === 0 && (
              <p className="rounded-box border border-dashed border-base-300 p-6 text-center text-sm opacity-70">
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
                  <p className="text-xs opacity-65">
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

      <RecentlyViewed exclude={product.slug} />

      {zoomed && product.image_url && (
        <Lightbox src={product.image_url} alt={product.name} onClose={() => setZoomed(false)} />
      )}

      {/* The buy bar: on a phone, whenever the button above has scrolled
          out of sight, the same button waits at the bottom edge — reading
          the reviews never means scrolling back up to buy. */}
      {docked && (
        <DockBar label="Buy this bake">
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold">{product.name}</p>
            <p className="figures text-sm">
              <PriceTag product={product} />
            </p>
          </div>
          <button
            type="button"
            className="btn btn-primary whitespace-nowrap"
            disabled={soldOut}
            onClick={addToBasket}
          >
            {soldOut ? "Sold out" : "Add to basket"}
          </button>
        </DockBar>
      )}
    </div>
  );
}
