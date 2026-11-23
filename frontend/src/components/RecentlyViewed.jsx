import { useEffect, useState } from "react";
import { Link } from "react-router";
import { readRecent } from "../lib/recent";
import PhotoTile from "./PhotoTile";

/* "That loaf from a minute ago" — the bakes this browser last opened, as a
   short row of ways back (lib/recent.js keeps the list; a product page adds
   itself to it). Photo and name only: the price lives on the bake's own
   page, where it is always the server's.

   One row to swipe on a phone, a strip of small tiles on a wide screen.
   Nothing at all until there is something to go back to. */
export default function RecentlyViewed({ exclude = null, title = "Recently viewed" }) {
  const [bakes, setBakes] = useState([]);

  useEffect(() => {
    setBakes(readRecent().filter((b) => b.slug !== exclude));
  }, [exclude]);

  if (bakes.length === 0) return null;

  return (
    <section className="mt-16" aria-label={title}>
      <h2 className="font-display mb-5 text-2xl font-semibold">{title}</h2>
      <div className="swipe-row -mx-4 gap-3 px-4 sm:mx-0 sm:grid sm:grid-cols-4 sm:px-0 lg:grid-cols-6">
        {bakes.slice(0, 6).map((b) => (
          <Link
            key={b.slug}
            to={`/products/${b.slug}`}
            className="group w-32 sm:w-auto"
          >
            <PhotoTile
              src={b.image_url}
              name={b.name}
              decorative
              className="aspect-square w-full rounded-box shadow-sm ring-1 ring-base-300/60"
            />
            <p className="mt-2 text-sm leading-snug font-semibold text-pretty group-hover:text-secondary">
              {b.name}
            </p>
          </Link>
        ))}
      </div>
    </section>
  );
}
