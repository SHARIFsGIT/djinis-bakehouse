import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { Bell, CalendarHeart, CheckCheck, ScrollText, Star, UserPlus } from "lucide-react";
import client from "../api/client";
import { formatDateTime, stampTime } from "../lib/format";

/* What each ping kind looks like on the panel. */
const KIND_STYLES = {
  order: { Icon: ScrollText, className: "bg-secondary/15 text-secondary" },
  review: { Icon: Star, className: "bg-warning/15 text-warning" },
  enquiry: { Icon: CalendarHeart, className: "bg-accent/20 text-accent" },
  customer: { Icon: UserPlus, className: "bg-success/15 text-success" },
};

function kindStyle(kind) {
  return KIND_STYLES[kind] ?? { Icon: Bell, className: "bg-base-200" };
}

/** The baker's bell: every order, review, catering lead and signup pings the
 *  desk. Polled quietly — a desk left open stays current without a refresh. */
export default function AdminBell() {
  const navigate = useNavigate();
  const [board, setBoard] = useState({ items: [], unread_count: 0 });
  const timer = useRef(null);
  const bellRef = useRef(null);
  // Where a phone-sized panel hangs from: just under the bell, measured when
  // it opens. The announcement bar above the navbar can appear or clear, so
  // the offset can't be a constant. Desktop never reads this variable.
  const [sheetTop, setSheetTop] = useState(undefined);

  const measure = useCallback(() => {
    const rect = bellRef.current?.getBoundingClientRect();
    if (rect) setSheetTop(`${Math.round(rect.bottom + 8)}px`);
  }, []);

  useEffect(() => {
    // A rotated phone or a resized window moves the bell — keep the sheet true.
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure]);

  const refresh = useCallback(() => {
    client
      .get("/admin/notifications", { params: { limit: 15 } })
      .then(({ data }) => setBoard(data))
      .catch(() => {}); // a missed poll is never worth a toast
  }, []);

  useEffect(() => {
    refresh();
    timer.current = setInterval(refresh, 30_000);
    return () => clearInterval(timer.current);
  }, [refresh]);

  const open = async (notification) => {
    // Read it and land where it points — the panel closes itself on blur.
    if (!notification.is_read) {
      setBoard((b) => ({
        ...b,
        unread_count: Math.max(0, b.unread_count - 1),
        items: b.items.map((n) => (n.id === notification.id ? { ...n, is_read: true } : n)),
      }));
      client.post(`/admin/notifications/${notification.id}/read`).catch(() => {});
    }
    if (notification.link) navigate(notification.link);
    document.activeElement?.blur();
  };

  const readAll = async () => {
    setBoard((b) => ({ ...b, unread_count: 0, items: b.items.map((n) => ({ ...n, is_read: true })) }));
    client.post("/admin/notifications/read-all").catch(() => {});
  };

  return (
    <div className="dropdown dropdown-end">
      <button
        type="button"
        tabIndex={0}
        ref={bellRef}
        onFocus={measure}
        className="btn btn-ghost btn-square relative"
        aria-label={`Notifications, ${board.unread_count} unread`}
      >
        <Bell size={20} />
        {board.unread_count > 0 && (
          <span className="badge badge-secondary badge-xs absolute -top-1 -right-1 border border-base-100 px-1.5 font-bold shadow">
            {board.unread_count > 99 ? "99+" : board.unread_count}
          </span>
        )}
      </button>

      {/* On phones the 22rem panel is wider than the viewport, so it detaches
          from the anchor and hangs as a fixed sheet from just under the bell —
          measured, because the announcement bar above can come and go. */}
      <div
        tabIndex={0}
        style={{ "--bell-top": sheetTop }}
        className="dropdown-content z-50 mt-2 w-88 rounded-box bg-base-100 shadow-xl ring-1 ring-base-300/70 max-sm:fixed max-sm:inset-x-3 max-sm:top-[var(--bell-top,5.5rem)] max-sm:mt-0 max-sm:w-auto"
      >
        <div className="flex items-center justify-between border-b border-base-300/60 px-4 py-3">
          <p className="font-display font-semibold">The desk bell</p>
          {board.unread_count > 0 && (
            <button
              type="button"
              className="btn btn-ghost btn-xs gap-1"
              onClick={readAll}
            >
              <CheckCheck size={13} /> Mark all read
            </button>
          )}
        </div>

        <ul className="max-h-96 overflow-y-auto max-sm:max-h-[calc(100dvh-var(--bell-top,5.5rem)-4.5rem)]">
          {board.items.length === 0 && (
            <li className="px-4 py-8 text-center text-sm opacity-55">
              All caught up — the shop is quiet.
            </li>
          )}
          {board.items.map((n) => {
            const { Icon, className } = kindStyle(n.kind);
            return (
              <li key={n.id} className="border-b border-base-200/70 last:border-0">
                <button
                  type="button"
                  onClick={() => open(n)}
                  className={`flex w-full items-start gap-3 px-4 py-3 text-left transition hover:bg-base-200/60 ${
                    n.is_read ? "opacity-60" : ""
                  }`}
                >
                  <span className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${className}`}>
                    <Icon size={15} aria-hidden="true" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold">{n.title}</span>
                    {n.body && (
                      <span className="mt-0.5 block text-xs leading-snug opacity-70">
                        {n.body}
                      </span>
                    )}
                    <span
                      className="mt-1 block text-[11px] opacity-50"
                      title={formatDateTime(n.created_at)}
                    >
                      {stampTime(n.created_at)}
                    </span>
                  </span>
                  {!n.is_read && (
                    <span className="mt-2 h-2 w-2 shrink-0 rounded-full bg-secondary" aria-label="unread" />
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
