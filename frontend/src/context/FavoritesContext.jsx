import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import client from "../api/client";
import { useAuth } from "./AuthContext";
import { useToast } from "./ToastContext";

const FavoritesContext = createContext(null);

/**
 * Saved bakes (the little heart). One list per account, loaded on login;
 * guests get a nudge toward the login page instead of a silent no-op.
 */
export function FavoritesProvider({ children }) {
  const { user, ready } = useAuth();
  const toast = useToast();
  const [ids, setIds] = useState(() => new Set());

  useEffect(() => {
    if (!ready) return;
    if (!user) {
      setIds(new Set());
      return;
    }
    let cancelled = false;
    client
      .get("/products/favorites")
      .then(({ data }) => {
        if (!cancelled) setIds(new Set(data.map((p) => p.id)));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [user, ready]);

  const toggle = useCallback(
    (product) => {
      if (!user) {
        toast.info("Log in to keep a list of the bakes you love");
        return;
      }
      const saved = ids.has(product.id);
      setIds((current) => {
        const next = new Set(current);
        if (saved) next.delete(product.id);
        else next.add(product.id);
        return next;
      });
      // Optimistic — the endpoint is idempotent, so a retry is harmless.
      const request = saved
        ? client.delete(`/products/${product.id}/favorite`)
        : client.put(`/products/${product.id}/favorite`);
      request.catch(() => {
        setIds((current) => {
          const next = new Set(current);
          if (saved) next.add(product.id);
          else next.delete(product.id);
          return next;
        });
        toast.error("Couldn't reach the shelf — try again");
      });
    },
    [ids, user, toast],
  );

  const value = useMemo(
    () => ({ isSaved: (id) => ids.has(id), toggle }),
    [ids, toggle],
  );

  return (
    <FavoritesContext.Provider value={value}>
      {children}
    </FavoritesContext.Provider>
  );
}

export function useFavorites() {
  return useContext(FavoritesContext);
}
