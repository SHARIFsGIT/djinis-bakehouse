import { NavLink, useNavigate } from "react-router";
import {
  CakeSlice,
  Calculator,
  Heart,
  LayoutDashboard,
  LogOut,
  MapPin,
  Megaphone,
  ScrollText,
  Settings,
  Star,
  Store,
  TicketPercent,
  User,
  Users,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";

const MY_TABS = [
  { to: "/account/orders", label: "Orders", Icon: ScrollText, end: false },
  { to: "/account/saved", label: "Saved bakes", Icon: Heart, end: true },
  { to: "/account/addresses", label: "Addresses", Icon: MapPin, end: true },
  { to: "/account/profile", label: "Profile", Icon: User, end: true },
];

// An admin runs the shop from this sidebar, so their own order history and
// wishlist stay out of it — just the personal details they may need to edit.
const MY_TABS_ADMIN = MY_TABS.filter(
  (t) => t.label !== "Orders" && t.label !== "Saved bakes",
);

// The desk, grouped the way a shift actually runs: glance at the day, work
// the trade, look after people, then tune the shop.
const DESK_SECTIONS = [
  {
    title: "Overview",
    tabs: [{ to: "/admin", label: "Dashboard", Icon: LayoutDashboard, end: true }],
  },
  {
    title: "Shop",
    tabs: [
      { to: "/admin/orders", label: "Orders", Icon: ScrollText, end: true },
      { to: "/admin/products", label: "Products", Icon: CakeSlice, end: true },
      { to: "/admin/costing", label: "Costing", Icon: Calculator, end: true },
      { to: "/admin/reviews", label: "Reviews", Icon: Star, end: true },
      { to: "/admin/promotions", label: "Promotions", Icon: TicketPercent, end: true },
    ],
  },
  {
    title: "Customers",
    tabs: [
      { to: "/admin/users", label: "Users", Icon: Users, end: true },
      { to: "/admin/marketing", label: "Marketing", Icon: Megaphone, end: true },
    ],
  },
  {
    title: "Setup",
    tabs: [{ to: "/admin/settings", label: "Settings", Icon: Settings, end: true }],
  },
];

// The way back out to the counter. end:true so the chip only lights up on
// the storefront itself, not on every route under it.
const STORE_TAB = { to: "/", label: "Store", Icon: Store, end: true };

// Hover = the active tab's own wine tint, one shade deeper — a quiet cue
// that stays inside the palette instead of shouting across it.
const ITEM =
  "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-semibold transition " +
  "hover:bg-secondary/20";

function Item({ to, label, Icon, end }) {
  return (
    <li>
      <NavLink
        to={to}
        end={end}
        className={({ isActive }) =>
          `${ITEM} ${isActive ? "bg-secondary/10 text-secondary" : ""}`
        }
      >
        <Icon size={18} className="shrink-0 opacity-70" />
        {label}
      </NavLink>
    </li>
  );
}

function Section({ title, tabs, first = false }) {
  return (
    <>
      <li
        className={`px-3 pb-1 text-[0.65rem] font-bold tracking-[0.18em] uppercase opacity-45 ${
          first ? "pt-1.5" : "pt-3"
        }`}
      >
        {title}
      </li>
      {tabs.map((t) => (
        <Item key={t.to} {...t} />
      ))}
    </>
  );
}

/* One sidebar for both corners. Admins get the whole desk grouped by how a
   shift runs — with their personal tabs beneath it. Customers simply see
   their own four. On a phone the card would stack a full screen tall above
   the work, so below lg it flattens into a single thumb-swipe rail instead;
   the grouped card returns at lg. Log out stays off the rail — the navbar's
   mobile menu already carries it. */
export default function CornerNav({ ariaLabel = "Account" }) {
  const { isAdmin, logout } = useAuth();
  const navigate = useNavigate();

  const railTabs = isAdmin
    ? [...DESK_SECTIONS.flatMap((s) => s.tabs), ...MY_TABS_ADMIN, STORE_TAB]
    : MY_TABS;

  return (
    <nav aria-label={ariaLabel}>
      {/* Phones: the whole desk as one horizontal shelf */}
      <ul className="-mx-4 flex items-center gap-2 overflow-x-auto px-4 pb-1 lg:hidden">
        {railTabs.map((t) => (
          <li key={`${t.to}-${t.label}`} className="shrink-0">
            <NavLink
              to={t.to}
              end={t.end}
              className={({ isActive }) =>
                `btn btn-sm gap-1.5 rounded-full whitespace-nowrap font-semibold ${
                  isActive
                    ? "btn-secondary text-secondary-content"
                    : "btn-ghost border border-base-300"
                }`
              }
            >
              <t.Icon size={14} className="shrink-0 opacity-70" aria-hidden="true" />
              {t.label}
            </NavLink>
          </li>
        ))}
      </ul>

      {/* lg up: the desk card, grouped the way a shift runs */}
      <ul className="hidden w-full space-y-0.5 rounded-box glass p-2 shadow-sm ring-1 ring-base-300/60 lg:block">
        {DESK_SECTIONS.map((section, i) =>
          isAdmin ? (
            <Section
              key={section.title}
              first={i === 0}
              title={section.title}
              tabs={section.tabs}
            />
          ) : null,
        )}
        {isAdmin ? (
          <Section title="My corner" tabs={MY_TABS_ADMIN} />
        ) : (
          MY_TABS.map((t) => <Item key={t.to} {...t} />)
        )}
        <li className="mt-2 space-y-0.5 border-t border-base-300/70 pt-2">
          {isAdmin && (
            <NavLink to="/" className={ITEM}>
              <Store size={18} className="shrink-0 opacity-70" />
              Store
            </NavLink>
          )}
          <button
            type="button"
            onClick={() => {
              logout();
              navigate("/");
            }}
            className={`${ITEM} w-full text-left text-error`}
          >
            <LogOut size={18} className="shrink-0 opacity-70" />
            Log out
          </button>
        </li>
      </ul>
    </nav>
  );
}
