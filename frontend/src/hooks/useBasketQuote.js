import { useEffect, useReducer, useRef } from "react";
import client from "../api/client";
import { basketKey, initialState, quoteReducer } from "../lib/quote";

/** The server-authoritative basket tally. Re-quotes (after a short debounce)
    whenever the basket's contents or coupon change; the reducer in lib/quote
    decides which answer the UI may trust. Returns { status, quote, error }
    where status is "idle" (empty basket) | "loading" | "ready" | "error". */
export default function useBasketQuote(items, couponCode) {
  const [state, dispatch] = useReducer(quoteReducer, initialState);
  const seq = useRef(0);
  const hasItems = items.length > 0;
  const key = basketKey(items, couponCode);

  useEffect(() => {
    if (!hasItems) {
      dispatch({ type: "empty" });
      return;
    }
    const mySeq = ++seq.current;
    dispatch({ type: "refresh", seq: mySeq });
    // Debounce: quantity nudges and coupon taps arrive in bursts; one quote
    // per settled basket is plenty (the endpoint is rate-limited, too).
    const timer = setTimeout(() => {
      client
        .post("/orders/quote", {
          items: items.map((i) => ({ product_id: i.product_id, quantity: i.quantity })),
          ...(couponCode ? { coupon_code: couponCode } : {}),
        })
        .then(({ data }) => dispatch({ type: "settled", seq: mySeq, quote: data }))
        .catch((error) => dispatch({ type: "failed", seq: mySeq, error }));
    }, 400);
    return () => clearTimeout(timer);
  }, [key, hasItems]);

  return state;
}
