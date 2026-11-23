import { Search } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router";
import client from "../api/client";
import { money } from "../lib/format";
import PhotoTile from "./PhotoTile";

/* The case search, with answers while you type: the first few bakes that
   match appear under the field, each one tap away, and Enter (or the last
   row) opens the full list. One component, worn in three places — the bar
   on a wide screen, the phone's menu sheet and the "page not found" page.

   It is a combobox to a keyboard and a screen reader: arrows walk the
   suggestions, Enter opens the one in hand, Escape puts them away. */

const MIN_LETTERS = 2;
const SUGGESTIONS = 5;

export default function SearchBox({ onNavigate, align = "end", shortcut = false }) {
  const navigate = useNavigate();
  const listId = useId();
  const field = useRef(null);
  const [term, setTerm] = useState("");
  const [matches, setMatches] = useState(null); // null = nothing asked yet
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const ticket = useRef(0);

  const wanted = term.trim();

  // Ask a beat after the typing stops; only the newest question may answer.
  useEffect(() => {
    if (wanted.length < MIN_LETTERS) {
      ticket.current += 1;
      setMatches(null);
      return undefined;
    }
    const mine = ++ticket.current;
    const timer = setTimeout(() => {
      client
        .get("/products", { params: { search: wanted, per_page: SUGGESTIONS } })
        .then(({ data }) => {
          if (mine !== ticket.current) return;
          setMatches(data.products ?? []);
          setActive(-1);
        })
        // A search that can't reach the shop offers nothing; Enter still
        // leads to the catalog, which says what went wrong.
        .catch(() => mine === ticket.current && setMatches(null));
    }, 180);
    return () => clearTimeout(timer);
  }, [wanted]);

  // "/" from anywhere on the page puts the cursor in the search — the
  // habit most sites with a search box have taught. Never while someone is
  // typing somewhere else, and never under an open dialog or drawer.
  useEffect(() => {
    if (!shortcut) return undefined;
    const onKey = (e) => {
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
      const el = e.target;
      if (el instanceof HTMLElement && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))) return;
      if (document.querySelector("dialog[open], [aria-modal='true']")) return;
      e.preventDefault();
      field.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [shortcut]);

  const leave = () => {
    setOpen(false);
    setActive(-1);
  };

  // A suggestion is chosen by a click — a finger that lands on one and then
  // drags is scrolling the page, not choosing (acting on the press itself
  // sent a scrolling thumb to a bake it never asked for). Pressing a
  // suggestion must not take the focus from the field, though, or the list
  // would close before the click arrives.
  const keepFocus = (e) => e.preventDefault();

  const go = (to) => {
    leave();
    setTerm("");
    setMatches(null);
    navigate(to);
    onNavigate?.();
  };

  const seeAll = () => go(wanted ? `/products?search=${encodeURIComponent(wanted)}` : "/products");

  // Rows a keyboard can stop on: each match, then "see everything".
  const rows = matches ? matches.length + (matches.length > 0 ? 1 : 0) : 0;
  const showing = open && matches !== null;

  const onKeyDown = (e) => {
    if (e.key === "Escape") {
      if (showing) e.preventDefault();
      leave();
      return;
    }
    if (!showing || rows === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (i + 1) % rows);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (i <= 0 ? rows - 1 : i - 1));
    }
  };

  const submit = (e) => {
    e.preventDefault();
    if (showing && active >= 0 && active < matches.length) go(`/products/${matches[active].slug}`);
    else seeAll();
  };

  return (
    <form onSubmit={submit} className="relative w-full" role="search">
      <label className="group input input-bordered input-sm flex w-full min-w-0 items-center gap-2 bg-base-100">
        <Search size={15} className="shrink-0 opacity-65" aria-hidden="true" />
        <input
          ref={field}
          type="search"
          className="min-w-0 grow"
          placeholder="Search the case…"
          value={term}
          onChange={(e) => {
            setTerm(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          // The suggestions hold the field's focus while they are pressed
          // (see `keepFocus`), so leaving the field never swallows a tap.
          onBlur={leave}
          onKeyDown={onKeyDown}
          aria-label="Search products"
          role="combobox"
          aria-expanded={showing}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={showing && active >= 0 ? `${listId}-${active}` : undefined}
          aria-keyshortcuts={shortcut ? "/" : undefined}
          autoComplete="off"
          enterKeyHint="search"
        />
        {/* The key that gets you here — shown until the field is in use,
            and not at all where there is no keyboard to press it on. */}
        {shortcut && !term && (
          <kbd className="kbd kbd-xs opacity-70 group-focus-within:hidden pointer-coarse:hidden" aria-hidden="true">
            /
          </kbd>
        )}
      </label>

      {showing && (
        <div
          id={listId}
          role="listbox"
          aria-label="Matching bakes"
          className={`fade-in absolute top-full z-10 mt-2 w-full min-w-72 overflow-hidden rounded-box border border-base-300 bg-base-100 shadow-xl ${
            align === "end" ? "right-0" : "left-0"
          }`}
        >
          {matches.length === 0 ? (
            <p className="px-4 py-4 text-sm opacity-70">
              Nothing in the case matches “{wanted}”.
            </p>
          ) : (
            <>
              {matches.map((p, i) => (
                <div
                  key={p.id}
                  id={`${listId}-${i}`}
                  role="option"
                  aria-selected={active === i}
                  onMouseDown={keepFocus}
                  onClick={() => go(`/products/${p.slug}`)}
                  onMouseEnter={() => setActive(i)}
                  className={`flex cursor-pointer items-center gap-3 px-3 py-2 ${
                    active === i ? "bg-base-200" : ""
                  }`}
                >
                  <PhotoTile src={p.image_url} name={p.name} decorative className="h-11 w-11 shrink-0 rounded-lg" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold">{p.name}</span>
                    <span className="block truncate text-xs opacity-70">
                      {p.stock > 0 ? p.category_name : "Sold out today"}
                    </span>
                  </span>
                  <span className="figures shrink-0 text-sm font-bold">{money(p.current_price)}</span>
                </div>
              ))}
              <div
                id={`${listId}-${matches.length}`}
                role="option"
                aria-selected={active === matches.length}
                onMouseDown={keepFocus}
                onClick={seeAll}
                onMouseEnter={() => setActive(matches.length)}
                className={`cursor-pointer border-t border-base-300/70 px-4 py-2.5 text-sm font-semibold text-secondary ${
                  active === matches.length ? "bg-base-200" : ""
                }`}
              >
                See everything for “{wanted}”
              </div>
            </>
          )}
        </div>
      )}
    </form>
  );
}
