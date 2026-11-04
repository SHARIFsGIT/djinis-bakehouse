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
  money,
  shopConfig,
  taxLabel,
} from "./format.js";

test("money renders each currency with its own symbol", () => {
  assert.equal(money(12.5, "usd"), "$12.50");
  assert.equal(money(12.5, "eur"), "€12.50");
  assert.equal(money(1234.5, "usd"), "$1,234.50");
  // Any other ISO code renders with its own symbol too.
  assert.equal(money(12.0, "gbp"), "£12.00");
});

test("money falls back to the shop's currency when an order carries none", () => {
  assert.equal(money(3.75), "$3.75"); // shopConfig default: USD
});

test("applyShopConfig retunes the labels and the currency", () => {
  applyShopConfig({
    currency: "eur",
    tax_rate: 0,
    shipping_fee: 3,
    free_shipping_threshold: 40,
  });
  assert.equal(shopConfig.currency, "EUR");
  assert.equal(money(9.99), "€9.99"); // no explicit order currency → the shop's
  assert.equal(taxLabel(), "Tax (0%)");
  // Restore the shipped defaults so later tests (and any importer in the same
  // process) see the shop as shipped.
  applyShopConfig({
    currency: "usd",
    tax_rate: 0.0875,
    shipping_fee: 5.99,
    free_shipping_threshold: 50,
  });
  assert.equal(money(1), "$1.00");
  assert.equal(taxLabel(), "Tax (8.75%)");
});
