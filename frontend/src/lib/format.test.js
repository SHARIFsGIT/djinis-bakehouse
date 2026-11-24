/* The money layer's own tests — plain node:test, no framework:
     node --test src/lib/format.test.js   (or: npm test)
   They pin what the euro migration leans on: the formatter speaks each
   order's own currency, and the config /store hands down retunes the labels
   and hints. The basket arithmetic itself is no longer mirrored here — it
   comes from POST /api/orders/quote (see quote.test.js). */

import test from "node:test";
import assert from "node:assert/strict";

import {
  ORDER_STATUSES,
  apiTime,
  applyShopConfig,
  couponSaving,
  hasNonActiveCurrency,
  money,
  parseAmountToCents,
  paysOnline,
  providerLabel,
  shopConfig,
  sinceLabel,
  statusGroup,
  statusLabel,
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

test("a coupon's saving reads in its own kind — percent or euro amount", () => {
  assert.equal(couponSaving({ code: "WELCOME10", percent_off: 10 }), "10% off");
  // A personalised fixed-value voucher: percent_off is 0 on the wire, and
  // "0% off" is exactly what the checkout used to print for it.
  assert.equal(
    couponSaving({ code: "THANKS5", percent_off: 0, fixed_amount_off: 5 }),
    "€5.00 off",
  );
  assert.equal(couponSaving({ percent_off: 15, fixed_amount_off: null }), "15% off");
});

test("an API timestamp with no zone on it is the UTC moment it was written as", () => {
  const moment = Date.UTC(2026, 10, 22, 10, 14, 3);
  // The server's own shape: UTC, unlabelled — with and without fractions.
  assert.equal(apiTime("2026-11-22T10:14:03").getTime(), moment);
  assert.equal(apiTime("2026-11-22T10:14:03.250000").getTime(), moment + 250);
  // A timestamp that names its zone is taken at its word, never shifted twice.
  assert.equal(apiTime("2026-11-22T10:14:03Z").getTime(), moment);
  assert.equal(apiTime("2026-11-22T11:14:03+01:00").getTime(), moment);
  assert.equal(apiTime("2026-11-22T05:14:03-0500").getTime(), moment);
});

test("typed refund amounts become exact cents, or nothing at all", () => {
  assert.equal(parseAmountToCents("12.50"), 1250);
  assert.equal(parseAmountToCents("12,5"), 1250); // the comma a German keyboard types
  assert.equal(parseAmountToCents(" 7 "), 700);
  assert.equal(parseAmountToCents("0.07"), 7);
  assert.equal(parseAmountToCents("0"), 0); // an amount — the caller refuses zero itself
  // The classic float trap: 19.99 * 100 is 1998.9999999999998.
  assert.equal(parseAmountToCents("19.99"), 1999);
  assert.equal(parseAmountToCents("4.35"), 435);
  // Not amounts: never rounded, never guessed.
  for (const bad of ["", "abc", "1.005", "-3", "1e3", "1,234.50", "€5", "5.", ".5", null, undefined]) {
    assert.equal(parseAmountToCents(bad), null, String(bad));
  }
});

test("only the provider rails pay online, and each provider has its name", () => {
  assert.equal(paysOnline("card"), true);
  assert.equal(paysOnline("paypal"), true);
  assert.equal(paysOnline("cash_on_delivery"), false);
  assert.equal(paysOnline(undefined), false);
  assert.equal(providerLabel("stripe"), "Stripe");
  assert.equal(providerLabel("paypal"), "PayPal");
  assert.equal(providerLabel(null), "the payment provider");
});

test("order statuses keep the API's values and fall into their badge groups", () => {
  // The wire values are the backend's OrderStatus, in the order a box moves.
  assert.deepEqual(
    ORDER_STATUSES.map((s) => s.value),
    ["pending", "confirmed", "baking", "ready", "out_for_delivery", "delivered", "cancelled"],
  );
  // Waiting on the shop; three in the kitchen; on its way; done; stopped.
  assert.deepEqual(
    Object.fromEntries(ORDER_STATUSES.map((s) => [s.value, statusGroup(s.value)])),
    {
      pending: "action",
      confirmed: "kitchen",
      baking: "kitchen",
      ready: "kitchen",
      out_for_delivery: "on-its-way",
      delivered: "done",
      cancelled: "stopped",
    },
  );
  assert.equal(statusGroup("unheard_of"), null);
  assert.equal(statusGroup(undefined), null);
  assert.equal(statusLabel("out_for_delivery"), "Out for delivery");
  assert.equal(statusLabel("unheard_of"), "Unheard of");
});

test("how long ago reads the way a person says it", () => {
  const now = new Date(2026, 10, 24, 9, 30); // Nov 24, 2026, 9:30 local
  const at = (y, m, d, h = 12) => new Date(y, m, d, h).toISOString();
  assert.equal(sinceLabel(at(2026, 10, 24, 6), now), "today");
  assert.equal(sinceLabel(at(2026, 10, 23, 23), now), "yesterday"); // calendar days, not 24 hours
  assert.equal(sinceLabel(at(2026, 10, 19), now), "5 days ago");
  assert.equal(sinceLabel(at(2026, 10, 3), now), "3 weeks ago");
  assert.match(sinceLabel(at(2026, 7, 1), now), /^Aug 1, 2026$/); // two months back: the date
  assert.equal(sinceLabel(null, now), "—");
});
