import { Star, StarHalf } from "lucide-react";

/** Read-only star display: full, half or outline per position. */
export default function RatingStars({ rating = 0, count, size = "text-sm" }) {
  const rounded = Math.round(rating * 2) / 2;
  return (
    <span className={`inline-flex items-center gap-1.5 ${size}`}>
      <span className="inline-flex text-warning" aria-hidden="true">
        {[1, 2, 3, 4, 5].map((i) => {
          if (rounded >= i) return <Star key={i} size="1.15em" className="fill-current" />;
          if (rounded >= i - 0.5) return <StarHalf key={i} size="1.15em" className="fill-current" />;
          return <Star key={i} size="1.15em" className="opacity-40" />;
        })}
      </span>
      {count !== undefined && (
        <span className="text-xs opacity-70">
          {rating > 0 ? rating.toFixed(1) : "New"}
          {count > 0 && ` (${count})`}
        </span>
      )}
    </span>
  );
}
