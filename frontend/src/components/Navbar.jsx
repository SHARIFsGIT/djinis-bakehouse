import { Menu, Moon, Search, ShoppingBasket, Sun, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import client from "../api/client";
import { useAuth } from "../context/AuthContext";
import { useCart } from "../context/CartContext";

const NAV_LINK =
  "text-sm font-semibold whitespace-nowrap opacity-80 transition hover:text-secondary hover:opacity-100";

/* The case search — one field, worn in two places (the bar and the mobile
   sheet), so the markup is written once. */
function SearchField({ value, onChange }) {
  return (
    <label className="input input-bordered input-sm flex w-full min-w-0 items-center gap-2 bg-base-100">
      <Search size={15} className="opacity-50" />
      <input
        type="search"
        className="grow"
        placeholder="Search the case…"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label="Search products"
      />
    </label>
  );
}

export default function Navbar() {
  const { user, isAdmin, logout } = useAuth();
  const { count } = useCart();
  const navigate = useNavigate();
  const location = useLocation();
  const [categories, setCategories] = useState([]);
  const [announcement, setAnnouncement] = useState("");
  const [search, setSearch] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);

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
    // Shop copy the admin can rewrite (or clear — an empty line hides the bar)
    client
      .get("/store")
      .then(({ data }) => setAnnouncement(data.announcement))
      .catch(() => {});
  }, []);

  const toggleTheme = () => {
    const next = theme === "bakery" ? "midnight" : "bakery";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem("theme", next);
    } catch {}
    setTheme(next);
  };

  const submitSearch = (e) => {
    e.preventDefault();
    const term = search.trim();
    navigate(
      term ? `/products?search=${encodeURIComponent(term)}` : "/products",
    );
    setMenuOpen(false);
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
      label: "On sale",
      active: browsing && saleActive,
    },
  ];

  return (
    <header className="glass sticky top-0 z-50 border-b border-base-300/60 lg:border-b-0">
      {/* Announcement bar — admin-editable, hidden when cleared */}
      {announcement && (
        <div className="brand-gradient text-primary-content">
          <p className="mx-auto max-w-7xl px-4 py-2 text-balance text-center text-xs font-semibold tracking-wide">
            {announcement}
          </p>
        </div>
      )}

      <nav className="navbar mx-auto max-w-7xl px-4 gap-2">
        {/* Brand */}
        <Link to="/" className="flex items-center gap-2.5">
          <img
            src="/static/site/logo.png"
            alt="Djini's Bakehouse"
            className="h-10 w-10 object-contain"
          />
          <span className="font-display text-xl leading-tight font-bold">
            Djini&rsquo;s{" "}
            <span className="italic text-secondary">Bakehouse</span>
          </span>
        </Link>

        <div className="flex-1" />

        {/* Search (desktop) — the shelves moved to their own line below,
            so the field can keep its full size at every width */}
        <form
          onSubmit={submitSearch}
          className="hidden min-w-0 lg:flex lg:w-44"
        >
          <SearchField value={search} onChange={setSearch} />
        </form>

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

        {/* Cart — the count rides the basket's outer shoulder, small enough
            that the basket itself stays fully visible */}
        <Link
          to="/cart"
          className="btn btn-ghost btn-sm btn-square relative"
          aria-label={`Basket, ${count} items`}
        >
          <ShoppingBasket size={20} />
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
                {user.first_name.charAt(0)}
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
            type="button"
            className="btn btn-ghost btn-sm btn-square"
            onClick={() => setMenuOpen((o) => !o)}
            aria-label="Toggle menu"
            aria-expanded={menuOpen}
          >
            {menuOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </nav>

      {/* Shelves — their own line under the bar, so the collection can grow
          without ever squeezing the search; extra shelves simply wrap to a
          second line. Matching double rules frame the row top and bottom.
          Below lg the shelves stay in the mobile menu. */}
      <div className="hidden lg:block">
        <div className="mx-auto max-w-7xl px-4">
          <div aria-hidden="true" className="border-t border-base-300/70" />
          <div aria-hidden="true" className="mt-0.5 border-t border-base-300/40" />
          <ul className="flex flex-wrap items-center justify-center gap-x-8 gap-y-1 py-2.5">
            {shopLinks.map((link) => (
              <li key={link.label}>
                <Link to={link.to} className={linkClass(link.active)}>
                  {link.label}
                </Link>
              </li>
            ))}
          </ul>
          <div aria-hidden="true" className="border-t border-base-300/40" />
          <div aria-hidden="true" className="mt-0.5 border-t border-base-300/70" />
        </div>
      </div>

      {/* Mobile menu */}
      {menuOpen && (
        <div className="glass border-t border-base-300/60 px-4 py-3 lg:hidden">
          <form onSubmit={submitSearch} className="mb-3">
            <SearchField value={search} onChange={setSearch} />
          </form>
          <ul className="space-y-0.5">
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
