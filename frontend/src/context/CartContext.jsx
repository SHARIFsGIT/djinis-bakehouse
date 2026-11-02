import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "./AuthContext";

const CartContext = createContext(null);
const CART_KEY = "bakehouse_cart";

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

  useEffect(() => {
    localStorage.setItem(CART_KEY, JSON.stringify(items));
  }, [items]);

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
      setItems((current) => {
        const existing = current.find((i) => i.product_id === product.id);
        if (existing) {
          return current.map((i) =>
            i.product_id === product.id
              ? { ...i, quantity: Math.min(i.quantity + quantity, Math.max(product.stock, 1)) }
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
            quantity: Math.min(quantity, Math.max(product.stock, 1)),
          },
        ];
      });
    };

    const setQuantity = (productId, quantity) => {
      setItems((current) =>
        current.map((i) =>
          i.product_id === productId
            ? { ...i, quantity: Math.max(1, Math.min(quantity, i.stock || 99)) }
            : i
        )
      );
    };

    const remove = (productId) =>
      setItems((current) => current.filter((i) => i.product_id !== productId));

    const clear = () => setItems([]);

    const count = items.reduce((sum, i) => sum + i.quantity, 0);
    const subtotal = items.reduce((sum, i) => sum + i.unit_price * i.quantity, 0);

    return { items, add, setQuantity, remove, clear, count, subtotal };
  }, [items]);

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart() {
  return useContext(CartContext);
}
