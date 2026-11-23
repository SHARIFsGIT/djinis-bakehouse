import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "./AuthContext";
import { MAX_LINE_QUANTITY } from "../lib/format";

const CartContext = createContext(null);
const CART_KEY = "bakehouse_cart";

/* The most of one bake a basket line can hold: what is on the shelf, and
   never more than the server takes on one line. */
const lineCap = (stock) => Math.min(Math.max(stock || 0, 1), MAX_LINE_QUANTITY);

function load() {
  try {
    const raw = localStorage.getItem(CART_KEY);
    const items = raw ? JSON.parse(raw) : [];
    return Array.isArray(items) ? items.filter((i) => i?.product_id && i?.quantity > 0) : [];
  } catch {
    return [];
  }
}

/**
 * The cart is intentionally client-side (guests can shop), but every price
 * and stock level is re-validated by the server at checkout.
 */
export function CartProvider({ children }) {
  const { user } = useAuth();
  const [items, setItems] = useState(load);
  // Counts the "add"s of this visit — the navbar's basket nods on each one.
  const [added, setAdded] = useState(0);

  useEffect(() => {
    try {
      localStorage.setItem(CART_KEY, JSON.stringify(items));
    } catch {
      // Storage refused (blocked, or full): the basket still works for this
      // visit — it just won't be here after a reload.
    }
  }, [items]);

  // The same basket in every tab: add a loaf in one, and the tab that is
  // sitting on the checkout page prices the same box.
  useEffect(() => {
    const onStorage = (event) => {
      if (event.key !== CART_KEY) return;
      setItems((current) =>
        JSON.stringify(current) === (event.newValue ?? "[]") ? current : load(),
      );
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  // The basket belongs to the person browsing. Logging out (or switching
  // accounts) empties it — but a guest's cart survives page reloads, so we
  // only clear on an actual person → different-person transition.
  const lastUserId = useRef(null);
  useEffect(() => {
    const id = user?.id ?? null;
    const previous = lastUserId.current;
    lastUserId.current = id;
    if (previous !== null && previous !== id) setItems([]);
  }, [user?.id]);

  const value = useMemo(() => {
    const add = (product, quantity = 1) => {
      setAdded((n) => n + 1);
      setItems((current) => {
        const existing = current.find((i) => i.product_id === product.id);
        if (existing) {
          return current.map((i) =>
            i.product_id === product.id
              ? { ...i, quantity: Math.min(i.quantity + quantity, lineCap(product.stock)) }
              : i
          );
        }
        return [
          ...current,
          {
            product_id: product.id,
            slug: product.slug,
            name: product.name,
            image_url: product.image_url ?? null,
            category_slug: product.category_slug,
            unit_price: product.current_price,
            stock: product.stock,
            quantity: Math.min(quantity, lineCap(product.stock)),
          },
        ];
      });
    };

    // `stock`, when given, is the shelf count the server just reported —
    // the figure cached when the bake was added can be days old.
    const setQuantity = (productId, quantity, stock) => {
      setItems((current) =>
        current.map((i) => {
          if (i.product_id !== productId) return i;
          const shelf = stock ?? i.stock;
          return {
            ...i,
            stock: shelf,
            quantity: Math.max(1, Math.min(quantity, lineCap(shelf || MAX_LINE_QUANTITY))),
          };
        })
      );
    };

    const remove = (productId) =>
      setItems((current) => current.filter((i) => i.product_id !== productId));

    const clear = () => setItems([]);

    const count = items.reduce((sum, i) => sum + i.quantity, 0);
    const subtotal = items.reduce((sum, i) => sum + i.unit_price * i.quantity, 0);

    return { items, add, setQuantity, remove, clear, count, subtotal, added };
  }, [items, added]);

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart() {
  return useContext(CartContext);
}
