import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router";
import client from "../api/client";
import RecentlyViewed from "../components/RecentlyViewed";
import SearchBox from "../components/SearchBox";
import { SHOP_NAME } from "../lib/titles";

/* A wrong turn should still lead somewhere: the search, the shelves, and
   whatever this visitor was looking at before — not one button back home. */
export default function NotFound() {
  const [shelves, setShelves] = useState([]);
  const { pathname } = useLocation();

  // The shell names every page it knows (lib/titles.js) and gives the rest
  // the shop's own line — which would leave a wrong address titled like the
  // home page. The tab says what happened instead. Keyed on the path so one
  // wrong turn after another is named each time (the shell's effect runs
  // first: it sits before the routes).
  useEffect(() => {
    document.title = `Page not found — ${SHOP_NAME}`;
  }, [pathname]);

  // A wrong address asks search engines not to keep it. Loaded directly,
  // the server's answer already says so (a 404 with noindex — app/seo.py);
  // reached from inside the app, where that tag has gone, the page says it
  // itself — once for each wrong turn.
  useEffect(() => {
    if (document.querySelector('meta[name="robots"][content="noindex"]')) return undefined;
    const meta = document.createElement("meta");
    meta.name = "robots";
    meta.content = "noindex";
    document.head.appendChild(meta);
    return () => meta.remove();
  }, [pathname]);

  useEffect(() => {
    client
      .get("/products/categories")
      .then(({ data }) => setShelves(data))
      .catch(() => {}); // the search and the way home are still there
  }, []);

  return (
    <div className="mx-auto max-w-7xl px-4 py-20">
      <div className="mx-auto max-w-2xl text-center">
        <h1 className="font-display text-6xl font-semibold" aria-label="Page not found">
          4<span className="text-secondary">0</span>4
        </h1>
        <p className="font-display mt-3 text-2xl italic opacity-80">This page is still proofing.</p>
        <p className="mt-3 opacity-70">
          Whatever you were looking for isn&rsquo;t in the case — but plenty of other good things are.
        </p>

        <div className="mx-auto mt-8 max-w-sm text-left">
          <SearchBox align="start" />
        </div>

        <div className="mt-6 flex flex-wrap justify-center gap-2">
          <Link to="/products" className="btn btn-primary">Explore Our Bakes</Link>
          {shelves.slice(0, 4).map((shelf) => (
            <Link
              key={shelf.slug}
              to={`/products?category=${shelf.slug}`}
              className="btn btn-ghost border border-base-300"
            >
              {shelf.name}
            </Link>
          ))}
          <Link to="/" className="btn btn-ghost border border-base-300">Home</Link>
        </div>
      </div>

      <RecentlyViewed title="You were looking at" />
    </div>
  );
}
