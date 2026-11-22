import { createContext, useCallback, useContext, useRef, useState } from "react";
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

  const push = useCallback((message, type = "info") => {
    const id = nextId.current++;
    setToasts((current) => [...current, { id, message, type }]);
    setTimeout(() => {
      setToasts((current) => current.filter((t) => t.id !== id));
    }, 4000);
  }, []);

  const toast = useRef({
    success: (m) => push(m, "success"),
    error: (m) => push(m, "error"),
    info: (m) => push(m, "info"),
  }).current;

  return (
    <ToastContext.Provider value={toast}>
      {children}
      {/* Above every dialog (DaisyUI's .modal sits at 999): a toast is often
          the only word on why a save inside one was refused. Announced to
          screen readers — politely for news, at once for errors. */}
      <div className="toast toast-end z-[1000] gap-2" role="status" aria-live="polite">
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
