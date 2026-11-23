import { createContext, useCallback, useContext, useRef, useState } from "react";
import { Link } from "react-router";
import { CircleAlert, CircleCheck, Info } from "lucide-react";

const ToastContext = createContext(null);

const STYLES = {
  success: { cls: "alert-success", Icon: CircleCheck },
  error: { cls: "alert-error", Icon: CircleAlert },
  info: { cls: "alert-info", Icon: Info },
};

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id) => {
    setToasts((current) => current.filter((t) => t.id !== id));
  }, []);

  // `action` ({ label, to }) is one way onward — "View basket" beside
  // "added to your basket" — so the news and the next step arrive together.
  const push = useCallback(
    (message, type = "info", { action = null } = {}) => {
      const id = nextId.current++;
      setToasts((current) => [...current, { id, message, type, action }]);
      // A toast that offers a step stays a little longer than one that only tells.
      setTimeout(() => dismiss(id), action ? 5500 : 4000);
    },
    [dismiss],
  );

  const toast = useRef({
    success: (m, options) => push(m, "success", options),
    error: (m, options) => push(m, "error", options),
    info: (m, options) => push(m, "info", options),
  }).current;

  return (
    <ToastContext.Provider value={toast}>
      {children}
      {/* Above every dialog (DaisyUI's .modal sits at 999): a toast is often
          the only word on why a save inside one was refused. Announced to
          screen readers — politely for news, at once for errors. */}
      <div
        className="toast toast-end z-[1000] gap-2"
        // Clear of a phone's home indicator, of whatever a page has docked
        // to the bottom edge (--dock, hooks/useDock.js) and of the
        // back-to-top button when it is out (--top-button) — the two share
        // a corner, and "View basket" must not end up under an arrow.
        style={{
          marginBottom:
            "calc(env(safe-area-inset-bottom, 0px) + var(--dock, 0px) + var(--top-button, 0px))",
        }}
        role="status"
        aria-live="polite"
      >
        {toasts.map((t) => {
          const { cls, Icon } = STYLES[t.type];
          return (
            <div
              key={t.id}
              className={`alert ${cls} animate-pop shadow-lg`}
              role={t.type === "error" ? "alert" : undefined}
            >
              <Icon size={18} className="shrink-0" aria-hidden="true" />
              <span className="text-sm font-semibold">{t.message}</span>
              {t.action && (
                <Link
                  to={t.action.to}
                  onClick={() => dismiss(t.id)}
                  className="btn btn-xs border-0 bg-base-100 text-base-content"
                >
                  {t.action.label}
                </Link>
              )}
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
