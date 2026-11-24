import { Menu, Moon, ShoppingBasket, Sun, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import client from "../api/client";
import useStore from "../hooks/useStore";
import { useAuth } from "../context/AuthContext";
import { useCart } from "../context/CartContext";
import AdminBell from "./AdminBell";
import SearchBox from "./SearchBox";

const NAV_LINK =
  "text-sm font-semibold whitespace-nowrap opacity-80 transition hover:text-secondary hover:opacity-100";

export default function Navbar() {
  const { user, isAdmin, logout } = useAuth();
  const { count, added } = useCart();
  const navigate = useNavigate();
  const location = useLocation();
  const [categories, setCategories] = useState([]);
  // Shop copy the admin can rewrite (or clear — an empty line hides the bar)
  const announcement = useStore()?.announcement ?? "";
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButton = useRef(null);
  const menuPanel = useRef(null);

  const [theme, setTheme] = useState(
    () =>
      document.documentElement.getAttribute("data-theme") ??
      (window.matchMedia?.("(prefers-color-scheme: dark)").matches
        ? "midnight"
        : "bakery"),
  );

  useEffect(() => {
    client
      .get("/products/categories")
      .then(({ data }) => setCategories(data))
      .catch(() => {});
  }, []);

  // The sheet is for getting somewhere — once the address changes (a shelf
  // link, the logo, the basket, the back button), it has done its job.
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname, location.search]);

  // Escape puts the phone menu away — unless something took the key first:
  // the search box closing its suggestions, or a dialog open over the page.
  // Focus that was in the menu goes back to its button instead of falling
  // to the top of the page with the links it was on.
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if (document.querySelector("dialog[open], [aria-modal='true']")) return;
      const active = document.activeElement;
      const inMenu = active === menuButton.current || menuPanel.current?.contains(active);
      setMenuOpen(false);
      if (inMenu) menuButton.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  const toggleTheme = () => {
    const next = theme === "bakery" ? "midnight" : "bakery";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem("theme", next);
    } catch {}
    setTheme(next);
  };

  const doLogout = () => {
    logout();
    navigate("/");
    setMenuOpen(false);
  };

  // The category links are query-param links on /products, so NavLink's own
  // active state can't tell them apart — derive it from the URL instead.
  const browsing = location.pathname === "/products";
  const params = new URLSearchParams(location.search);
  const activeCategory = params.get("category") ?? "";
  const saleActive = params.get("on_sale") === "true";

  const linkClass = (active) =>
    `${NAV_LINK} ${active ? "text-secondary opacity-100 underline decoration-2 underline-offset-8" : ""}`;

  const shopLinks = [
    {
      to: "/products",
      label: "Explore Our Bakes",
      active: browsing && !activeCategory && !saleActive,
    },
    ...categories.map((c) => ({
      to: `/products?category=${c.slug}`,
      label: c.name,
      active: browsing && activeCategory === c.slug,
    })),
    {
      to: "/products?on_sale=true",
      label: "On Sale",
      active: browsing && saleActive,
    },
  ];

  return (
    // The trailing "!" matters: DaisyUI's own .glass utility resets borders
    // and sorts after plain border utilities, so the header's hairline has
    // to win explicitly.
    <header className="glass sticky top-0 z-50 border-b! border-base-300/60!">
      {/* Announcement bar — admin-editable, hidden when cleared */}
      {announcement && (
        <div className="brand-gradient text-primary-content">
          <p className="mx-auto max-w-7xl px-4 py-2 text-balance text-center text-xs font-semibold tracking-wide">
            {announcement}
          </p>
        </div>
      )}

      {/* Tight on a phone: the baker's bar carries five controls there
          (theme, bell, basket, corner, menu). */}
      <nav className="navbar mx-auto max-w-7xl gap-0 px-3 sm:gap-2 sm:px-4">
        {/* Brand. On a phone the link takes whatever the controls leave. The
            mark always shows; the name beside it shows when it fits — on one
            line, or on two — and otherwise wraps onto a second row that the
            link's fixed height hides (the row gap keeps it out of sight).
            So there is no breakpoint to keep in step with how many controls
            a visitor has: a guest's "Log in", the baker's bell. The name
            simply never runs underneath them, which at 320px it used to.
            From a tablet up there is always room and it is an ordinary row. */}
        <Link
          to="/"
          aria-label="Djini's Bakehouse — home"
          className="flex h-[3.25rem] min-w-0 flex-1 flex-wrap content-start items-center gap-x-2 gap-y-6 overflow-hidden pt-1.5 sm:h-auto sm:flex-none sm:flex-nowrap sm:gap-x-2.5 sm:overflow-visible sm:pt-0"
        >
          <img
            src="/static/site/logo.png"
            alt=""
            className="h-10 w-10 shrink-0 object-contain"
          />
          {/* 5.6em: the width of "Bakehouse", the longer of the two words */}
          <span className="font-display min-w-[5.6em] flex-1 basis-[5.6em] text-lg leading-tight font-bold sm:min-w-0 sm:flex-none sm:basis-auto sm:text-xl">
            Djini&rsquo;s{" "}
            <span className="italic text-secondary">Bakehouse</span>
          </span>
        </Link>

        <div className="hidden flex-1 sm:block" />

        {/* Search (desktop) — the shelves moved to their own line below,
            so the field can keep its full size at every width */}
        <div className="hidden min-w-0 lg:flex lg:w-52">
          <SearchBox shortcut />
        </div>

        {/* Theme toggle */}
        <button
          type="button"
          onClick={toggleTheme}
          className="btn btn-ghost btn-sm btn-square"
          aria-label="Toggle color theme"
          title={
            theme === "bakery" ? "Switch to night mode" : "Switch to day mode"
          }
        >
          {theme === "bakery" ? <Moon size={19} /> : <Sun size={19} />}
        </button>

        {/* The desk bell — rides the navbar so it follows the baker around
            the shop, not just the back office */}
        {isAdmin && <AdminBell />}

        {/* Cart — the count rides the basket's outer shoulder, small enough
            that the basket itself stays fully visible */}
        <Link
          to="/cart"
          className="btn btn-ghost btn-sm btn-square relative"
          aria-label={`Basket, ${count} ${count === 1 ? "item" : "items"}`}
        >
          {/* Re-keyed on every "add", so the nod plays each time. */}
          <span key={added} className={added > 0 ? "basket-nod inline-flex" : "inline-flex"}>
            <ShoppingBasket size={20} aria-hidden="true" />
          </span>
          {count > 0 && (
            <span className="badge badge-secondary badge-xs absolute -top-1.5 -right-1.5 border border-base-100 px-1.5 font-bold shadow">
              {count > 99 ? "99+" : count}
            </span>
          )}
        </Link>

        {/* Profile — straight through to the corner (the desk, for the
            baker), no dropdown */}
        {user ? (
          <Link
            to={isAdmin ? "/admin" : "/account/orders"}
            className="btn btn-ghost btn-sm gap-2 px-2"
            aria-label="My corner"
            title="My corner"
          >
            {user.avatar_url ? (
              <img
                src={user.avatar_url}
                alt=""
                className="h-7 w-7 rounded-full object-cover ring-1 ring-base-300"
              />
            ) : (
              <span className="brand-gradient flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold text-secondary-content">
                {(user.first_name || user.email || "?").charAt(0).toUpperCase()}
              </span>
            )}
            <span className="hidden max-w-24 truncate text-sm font-semibold xl:inline">
              {user.first_name}
            </span>
          </Link>
        ) : (
          <Link to="/login" className="btn btn-primary btn-sm">
            Log in
          </Link>
        )}

        {/* Mobile menu button */}
        <div className="lg:hidden">
          <button
            ref={menuButton}
            type="button"
            className="btn btn-ghost btn-sm btn-square"
            onClick={() => setMenuOpen((o) => !o)}
            aria-label="Toggle menu"
            aria-expanded={menuOpen}
            aria-controls={menuOpen ? "site-menu" : undefined}
          >
            {menuOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </nav>

      {/* Shelves — their own line under the bar, centered and framed by a
          hairline on each side (the header's own border-b closes the bottom).
          Extra shelves simply wrap to a second line. Below lg the shelves
          stay in the mobile menu. */}
      <div className="hidden border-t border-base-300/60 lg:block">
        <ul className="mx-auto flex max-w-7xl flex-wrap items-center justify-center gap-x-8 gap-y-1 px-4 py-3">
          {shopLinks.map((link) => (
            <li key={link.label}>
              <Link to={link.to} className={linkClass(link.active)}>
                {link.label}
              </Link>
            </li>
          ))}
        </ul>
      </div>

      {/* Mobile menu */}
      {menuOpen && (
        <div ref={menuPanel} id="site-menu" className="glass border-t border-base-300/60 px-4 py-3 lg:hidden">
          <div className="mb-3">
            <SearchBox align="start" onNavigate={() => setMenuOpen(false)} />
          </div>
          {/* The header is pinned to the top of the screen, so a list longer
              than a small phone is tall would have no way to be scrolled to
              its end. The list scrolls inside itself instead (the search
              stays outside it, so its suggestions are never clipped). */}
          <ul className="max-h-[calc(100dvh-12rem)] space-y-0.5 overflow-y-auto overscroll-contain">
            {shopLinks.map((link) => (
              <li key={link.label}>
                <Link
                  to={link.to}
                  onClick={() => setMenuOpen(false)}
                  className={`block rounded-lg px-3 py-2 text-sm font-semibold transition hover:bg-base-200 ${
                    link.active ? "text-secondary" : ""
                  }`}
                >
                  {link.label}
                </Link>
              </li>
            ))}
            {user && (
              <>
                <li className="pt-2" />
                <li>
                  <Link
                    to={isAdmin ? "/admin" : "/account/orders"}
                    onClick={() => setMenuOpen(false)}
                    className="block rounded-lg px-3 py-2 text-sm font-semibold transition hover:bg-base-200"
                  >
                    My corner
                  </Link>
                </li>
                <li>
                  <button
                    type="button"
                    onClick={doLogout}
                    className="block w-full rounded-lg px-3 py-2 text-left text-sm font-semibold transition hover:bg-base-200 hover:text-error"
                  >
                    Log out
                  </button>
                </li>
              </>
            )}
          </ul>
        </div>
      )}
    </header>
  );
}
