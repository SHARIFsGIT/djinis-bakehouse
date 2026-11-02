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
      <div className="toast toast-end z-[90] gap-2">
        {toasts.map((t) => {
          const { cls, Icon } = STYLES[t.type];
          return (
            <div key={t.id} className={`alert ${cls} animate-pop shadow-lg`}>
              <Icon size={18} className="shrink-0" />
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
