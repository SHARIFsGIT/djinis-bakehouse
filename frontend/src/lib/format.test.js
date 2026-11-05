/* The money layer's own tests — plain node:test, no framework:
     node --test src/lib/format.test.js   (or: npm test)
   They pin what the euro migration leans on: the formatter speaks each
   order's own currency, and the config /store hands down retunes the labels
   and hints. The basket arithmetic itself is no longer mirrored here — it
   comes from POST /api/orders/quote (see quote.test.js). */

import test from "node:test";
import assert from "node:assert/strict";

import {
  applyShopConfig,
  hasNonActiveCurrency,
  money,
  shopConfig,
  taxLabel,
} from "./format.js";

/* Restore the shipped defaults around each test that retunes the config —
   the module is shared state and later importers must see the shop as
   shipped: EUR, with every money rule still pending approval (null). */
function resetShopConfig() {
  shopConfig.currency = "EUR";
  shopConfig.taxRate = null;
  shopConfig.shippingFee = null;
  shopConfig.freeShippingThreshold = null;
}

test("money renders each currency with its own symbol", () => {
  assert.equal(money(12.5, "usd"), "$12.50");
  assert.equal(money(12.5, "eur"), "€12.50");
  assert.equal(money(1234.5, "usd"), "$1,234.50");
  // Any other ISO code renders with its own symbol too.
  assert.equal(money(12.0, "gbp"), "£12.00");
});

test("money falls back to the shop's currency when an order carries none", () => {
  // The shipped default is euro — the 2026-11-05 cutover.
  assert.equal(money(3.75), "€3.75");
});

test("money never crashes or invents a currency for unusable codes", () => {
  // The backend's "unknown" bucket and any corrupted row render as what
  // they are: a plain figure with the raw code — never $, never €, never
  // a RangeError taking the page down.
  assert.equal(money(12.5, "unknown"), "12.50 UNKNOWN");
  assert.equal(money(12.5, "bitcoin"), "12.50 BITCOIN");
  assert.equal(money(12.5, "US$"), "12.50 US$"); // malformed
  assert.equal(money(12.5, ""), "12.50"); // blank — nothing useful to name
  assert.equal(money(12.5, null), "€12.50"); // absent → the shop's own
  assert.equal(money(12.5, undefined), "€12.50");
  // A padded code is still its currency, not a mystery bucket.
  assert.equal(money(12.5, " gbp "), "£12.50");
  // Whatever a broken row throws at it, money() returns a string.
  for (const bad of ["unknown", "", "bitcoin", "US$", "e", 7]) {
    assert.equal(typeof money(1, bad), "string");
  }
});

test("the split appears exactly when money sits outside the active currency", () => {
  // One non-active bucket is enough — after the cutover the old currency's
  // book must stay visible even when it's the only one there is.
  assert.equal(hasNonActiveCurrency(["eur"]), false); // all-active book
  assert.equal(hasNonActiveCurrency([]), false); // no orders at all
  assert.equal(hasNonActiveCurrency(["usd"]), true); // history-only
  assert.equal(hasNonActiveCurrency(["eur", "usd"]), true);
  assert.equal(hasNonActiveCurrency(["unknown"]), true); // stays visible
  // Case-insensitive in both directions: API lowercase, config upper.
  assert.equal(hasNonActiveCurrency(["EUR"], "eur"), false);
  assert.equal(hasNonActiveCurrency(["EUR"], "usd"), true);
});

test("applyShopConfig retunes the labels and the currency", () => {
  applyShopConfig({
    currency: "eur",
    tax_rate: 0.05,
    shipping_fee: 3.5,
    free_shipping_threshold: 30,
  });
  assert.equal(shopConfig.currency, "EUR");
  assert.equal(money(9.99), "€9.99"); // no explicit order currency → the shop's
  assert.equal(taxLabel(), "Tax (5%)");
  resetShopConfig();
  assert.equal(money(1), "€1.00");
});

test("pending rules stay pending — null is not zero", () => {
  // The shop ships with every money rule unapproved: /store says null for
  // each, and Number(null) === 0 must not turn that into a 0% tax rate, a
  // free delivery, or a zero threshold. A null from the server clears any
  // stale approved value rather than keeping it.
  applyShopConfig({
    currency: "eur",
    tax_rate: null,
    shipping_fee: null,
    free_shipping_threshold: null,
  });
  assert.equal(shopConfig.taxRate, null);
  assert.equal(shopConfig.shippingFee, null);
  assert.equal(shopConfig.freeShippingThreshold, null);
  // No approved rate → the row is named plainly, never "Tax (0%)".
  assert.equal(taxLabel(), "Tax");

  // An approved figure applied first is genuinely cleared by a later null.
  applyShopConfig({ currency: "eur", tax_rate: 0.05 });
  assert.equal(shopConfig.taxRate, 0.05);
  applyShopConfig({ currency: "eur", tax_rate: null });
  assert.equal(shopConfig.taxRate, null);
  resetShopConfig();
});
