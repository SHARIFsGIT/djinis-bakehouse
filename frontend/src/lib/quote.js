/* The basket-quote state machine — the rules that decide which server quote
   the UI may trust. The basket's money figures all come from
   POST /api/orders/quote now; this module keeps the discipline around them:

   - a response for an older basket never overwrites a newer one (every
     request carries a sequence number; only the latest wins),
   - the last confirmed quote stays visible while a new one is fetched, but
     nothing may be submitted against it until the fresh one lands,
   - a failed refresh retires the old quote too — stale totals must never
     outlive the basket they were computed for. */

export const initialState = { status: "idle", quote: null, error: null, seq: 0 };

/** Identity of the basket being priced: what's in it (order irrelevant —
    the server totals the same basket either way) and which code rides on
    it. The hook re-quotes only when this key changes. */
export const basketKey = (items, couponCode) =>
  `${(items ?? [])
    .map((i) => `${i.product_id}x${i.quantity}`)
    .sort()
    .join("|")}#${(couponCode ?? "").trim().toUpperCase()}`;

export function quoteReducer(state, action) {
  switch (action.type) {
    case "refresh": // a new basket is being priced (request seq n)
      return { ...state, status: "loading", error: null, seq: action.seq };
    case "settled": // the server priced basket seq n
      if (action.seq !== state.seq) return state; // an older basket answered late
      return { status: "ready", quote: action.quote, error: null, seq: state.seq };
    case "failed": // the server refused basket seq n
      if (action.seq !== state.seq) return state;
      return { status: "error", quote: null, error: action.error, seq: state.seq };
    case "empty": // nothing in the basket — nothing to price
      return initialState;
    default:
      return state;
  }
}

/** Checkout may only proceed against a quote that is both fresh (ready, not
    mid-refresh) and present. Loading, error and empty all say no. */
export const canSubmitWith = (state) => state.status === "ready" && state.quote !== null;
