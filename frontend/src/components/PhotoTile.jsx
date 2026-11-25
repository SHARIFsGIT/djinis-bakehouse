import { useLayoutEffect, useRef, useState } from "react";
import { productInitial } from "../lib/format";

/**
 * Product photo with a plain letter-tile fallback for items that have no
 * image yet (e.g. something an admin just added) — or whose image turned
 * out not to load. The photo holds its place as a quiet tile and fades in
 * once it has arrived, instead of painting itself top to bottom.
 *
 * `decorative` is for a thumbnail that sits right beside the bake's name:
 * a screen reader hears the name once, not twice. `eager` is for the
 * photos a page opens on — fetched at once and ahead of the rest, since
 * they are the largest thing the shopper waits to see.
 */
export default function PhotoTile({ src, name = "", className = "", decorative = false, eager = false }) {
  const img = useRef(null);
  const [loaded, setLoaded] = useState(false);
  const [broken, setBroken] = useState(false);

  // A photo already in the browser's cache is complete before React ever
  // attaches onLoad — so it is asked directly, before the first paint: a
  // cached photo is simply there, and only one still on the wire fades in.
  useLayoutEffect(() => {
    setBroken(false);
    setLoaded(Boolean(img.current?.complete && img.current.naturalWidth > 0));
  }, [src]);

  return (
    <div className={`photo-tile ${className}`}>
      {src && !broken ? (
        <img
          ref={img}
          src={src}
          alt={decorative ? "" : name}
          loading={eager ? "eager" : "lazy"}
          fetchPriority={eager ? "high" : undefined}
          decoding="async"
          data-loaded={loaded}
          onLoad={() => setLoaded(true)}
          onError={() => setBroken(true)}
        />
      ) : (
        <div className="flex h-full w-full items-center justify-center bg-base-200">
          <span className="font-display text-4xl font-bold text-base-content/30" aria-hidden={decorative}>
            {productInitial(name)}
          </span>
        </div>
      )}
    </div>
  );
}
