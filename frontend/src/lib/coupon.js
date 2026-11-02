/**
 * The voucher a shopper tapped on the basket page, carried to checkout.
 * Deliberately plain localStorage — it's a convenience, not money; the
 * server re-validates the code (and its spend threshold) at checkout.
 */
const KEY = "bakehouse_coupon";

export function getStoredCoupon() {
  try {
    return localStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

export function storeCoupon(code) {
  try {
    if (code) localStorage.setItem(KEY, code);
    else localStorage.removeItem(KEY);
  } catch {
    // private mode etc. — checkout still has the manual code box
  }
}
