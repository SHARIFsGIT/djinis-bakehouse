import { Link } from "react-router";
import { Heart } from "lucide-react";
import { useCart } from "../context/CartContext";
import { useFavorites } from "../context/FavoritesContext";
import { useToast } from "../context/ToastContext";
import PriceTag from "./PriceTag";
import PhotoTile from "./PhotoTile";
import RatingStars from "./RatingStars";

export default function ProductCard({ product }) {
  const { add } = useCart();
  const toast = useToast();
  const { isSaved, toggle } = useFavorites();
  const saved = isSaved(product.id);
  const soldOut = product.stock <= 0;
  const lowStock = !soldOut && product.stock <= 5;

  return (
    <div className="card glass group shadow-sm ring-1 ring-base-300/60">
      <figure className="relative">
        <Link
          to={`/products/${product.slug}`}
          className="relative block aspect-[3/4] w-full"
        >
          <PhotoTile
            src={product.image_url}
            name={product.name}
            className="absolute inset-0"
          />
          {product.is_on_sale && (
            <span className="badge badge-secondary absolute top-3 left-3 font-display font-bold shadow">
              −{product.discount_percentage}%
            </span>
          )}
          {soldOut && (
            <span className="absolute inset-0 flex items-center justify-center bg-base-100/70 font-display text-xl font-bold text-base-content/80">
              Sold out for today
            </span>
          )}
          {product.is_featured && !product.is_on_sale && (
            <span className="badge badge-primary absolute bottom-3 left-3 font-semibold shadow">
              House favorite
            </span>
          )}
          {lowStock && (
            <span className="badge badge-secondary badge-sm absolute right-3 bottom-3 font-semibold shadow">
              Only {product.stock} left
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
          {saved ? (
            <Heart size={16} className="fill-current" />
          ) : (
            <Heart size={16} />
          )}
        </button>
      </figure>

      <div className="card-body gap-2 p-4">
        <div className="flex items-start justify-between gap-2">
          <Link
            to={`/products/${product.slug}`}
            className="font-display text-lg leading-tight font-semibold hover:text-secondary"
          >
            {product.name}
          </Link>
        </div>
        <RatingStars
          rating={product.average_rating}
          count={product.review_count}
        />
        <div className="mt-1 flex flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:justify-between">
          <PriceTag product={product} />
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={soldOut}
            onClick={() => {
              add(product, 1);
              toast.success(`${product.name} added to your basket`);
            }}
          >
            {soldOut ? "Sold out" : "Add to basket"}
          </button>
        </div>
      </div>
    </div>
  );
}
