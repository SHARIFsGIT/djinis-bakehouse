import { productInitial } from "../lib/format";

/**
 * Product photo with a plain letter-tile fallback for items that have no
 * image yet (e.g. something an admin just added).
 */
export default function PhotoTile({ src, name = "", className = "" }) {
  return (
    <div className={`photo-tile ${className}`}>
      {src ? (
        <img src={src} alt={name} loading="lazy" />
      ) : (
        <div className="flex h-full w-full items-center justify-center bg-base-200">
          <span className="font-display text-4xl font-bold text-base-content/30">
            {productInitial(name)}
          </span>
        </div>
      )}
    </div>
  );
}
