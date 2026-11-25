import { Link } from "react-router";
import { Heart } from "lucide-react";
import { useCart } from "../context/CartContext";
import { useFavorites } from "../context/FavoritesContext";
import { useToast } from "../context/ToastContext";
import PriceTag from "./PriceTag";
import PhotoTile from "./PhotoTile";
import RatingStars from "./RatingStars";

export default function ProductCard({ product, eager = false }) {
  const { add } = useCart();
  const toast = useToast();
  const { isSaved, toggle } = useFavorites();
  const saved = isSaved(product.id);
  const soldOut = product.stock <= 0;
  const lowStock = !soldOut && product.stock <= 5;

  return (
    // A column that fills its grid cell: however many lines a name takes,
    // the price and the button sit on the card's bottom edge, level with
    // the cards beside it.
    <div className="card glass group h-full shadow-sm ring-1 ring-base-300/60">
      <figure className="relative">
        <Link
          to={`/products/${product.slug}`}
          className="relative block aspect-[3/4] w-full"
        >
          <PhotoTile
            src={product.image_url}
            name={product.name}
            className="absolute inset-0"
            eager={eager}
          />
          {product.is_on_sale && (
            <span className="badge badge-secondary absolute top-3 left-3 font-display font-bold shadow">
              −{product.discount_percentage}%
            </span>
          )}
          {soldOut && (
            <span className="absolute inset-0 flex items-center justify-center bg-base-100/70 px-3 text-center font-display text-base leading-snug font-bold text-balance text-base-content/80 sm:text-xl">
              Sold out for today
            </span>
          )}
          {/* The two bottom badges share one row that wraps: on a narrow
              card a favourite that is also running low printed one badge
              over the other. */}
          {((product.is_featured && !product.is_on_sale) || lowStock) && (
            <span className="absolute inset-x-3 bottom-3 flex flex-wrap items-end gap-1.5">
              {product.is_featured && !product.is_on_sale && (
                <span className="badge badge-primary font-semibold whitespace-nowrap shadow">House favorite</span>
              )}
              {lowStock && (
                <span className="badge badge-secondary badge-sm ml-auto font-semibold whitespace-nowrap shadow">
                  Only {product.stock} left
                </span>
              )}
            </span>
          )}
        </Link>
        <button
          type="button"
          className={`btn btn-circle btn-sm absolute top-3 right-3 border-0 shadow ${
            saved
              ? "brand-gradient text-secondary-content"
              : "bg-base-100/90 text-base-content"
          }`}
          onClick={() => toggle(product)}
          aria-label={
            saved
              ? `Remove ${product.name} from saved bakes`
              : `Save ${product.name} for later`
          }
          aria-pressed={saved}
        >
          {/* Re-keyed when it flips, so the heart pops each time it is set. */}
          <Heart key={String(saved)} size={16} className={saved ? "animate-pop fill-current" : ""} aria-hidden="true" />
        </button>
      </figure>

      <div className="card-body @container flex-1 gap-2 p-4">
        <Link
          to={`/products/${product.slug}`}
          className="font-display text-lg leading-tight font-semibold text-pretty hover:text-secondary"
        >
          {product.name}
        </Link>
        <RatingStars
          rating={product.average_rating}
          count={product.review_count}
        />
        {/* Price and button share a line only when the card itself has the
            room — four cards across a tablet are as narrow as two on a
            phone, and there the button ran out of the card. A price too long
            for the line puts the button under it rather than past the edge. */}
        <div className="mt-auto flex flex-col items-stretch gap-2 pt-1 sm:@min-[10rem]:flex-row sm:@min-[10rem]:flex-wrap sm:@min-[10rem]:items-center sm:@min-[10rem]:justify-between">
          <PriceTag product={product} />
          <button
            type="button"
            className="btn btn-primary btn-sm whitespace-nowrap"
            disabled={soldOut}
            onClick={() => {
              add(product, 1);
              toast.success(`${product.name} is in your basket`, {
                action: { label: "View basket", to: "/cart" },
              });
            }}
          >
            {soldOut ? "Sold out" : "Add to basket"}
          </button>
        </div>
      </div>
    </div>
  );
}
