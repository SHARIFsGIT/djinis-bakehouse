/* The browser-tab titles — plain node:test (npm test). */

import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_DESCRIPTION,
  DEFAULT_TITLE,
  clipDescription,
  productDescription,
  shelfTitle,
  titleFor,
} from "./titles.js";

test("the home page carries the shop's own line", () => {
  assert.equal(titleFor("/"), DEFAULT_TITLE);
  assert.equal(titleFor(""), DEFAULT_TITLE);
  assert.equal(titleFor(undefined), DEFAULT_TITLE);
});

test("each page is named, with the shop after it", () => {
  assert.equal(titleFor("/cart"), "Your basket — Djini's Bakehouse");
  assert.equal(titleFor("/checkout"), "Checkout — Djini's Bakehouse");
  assert.equal(titleFor("/imprint"), "Legal notice — Djini's Bakehouse");
  assert.equal(titleFor("/impressum"), "Legal notice — Djini's Bakehouse");
  assert.equal(titleFor("/contact/"), "Find us — Djini's Bakehouse");
});

test("pages under a section take the section's name; the more specific entry wins", () => {
  assert.equal(titleFor("/account/orders/12"), "Your orders — Djini's Bakehouse");
  assert.equal(titleFor("/account/orders/12/receipt"), "Your orders — Djini's Bakehouse");
  assert.equal(titleFor("/account/saved"), "Saved bakes — Djini's Bakehouse");
  assert.equal(titleFor("/admin"), "Baker's desk — Djini's Bakehouse");
  assert.equal(titleFor("/admin/orders"), "Desk: Orders — Djini's Bakehouse");
  // a product page starts on the catalog's name; the page then writes the bake's own
  assert.equal(titleFor("/products/butter-croissant"), "Explore our bakes — Djini's Bakehouse");
});

test("a prefix is a whole path segment, never a look-alike", () => {
  assert.equal(titleFor("/cartoon"), DEFAULT_TITLE);
  assert.equal(titleFor("/administrator"), DEFAULT_TITLE);
  assert.equal(titleFor("/no-such-page"), DEFAULT_TITLE);
});

/* The same cases are in the backend's tests/test_seo.py — the server writes
   each page's first head by the same rules (backend/app/seo.py). */

test("a description is folded, and cut at a word when it runs long", () => {
  assert.equal(clipDescription("  short \n text  "), "short text");
  assert.equal(clipDescription("one two three four", 10), "one two…");
  assert.equal(clipDescription("one, two, three", 10), "one, two…");
  assert.equal(clipDescription("abcdefghijklmnop", 10), "abcdefghi…");
  const long = "Laminated over two days with cultured butter, then rolled and folded again. ".repeat(4);
  const clipped = clipDescription(long);
  assert.ok(clipped.length <= 160 && clipped.endsWith("…") && long.startsWith(clipped.slice(0, -1)));
});

test("a product's description is its own, then its shelf when that fits", () => {
  assert.equal(
    productDescription("Laminated over two days.", "Pastries"),
    "Laminated over two days. Pastries from Djini's Bakehouse.",
  );
  assert.equal(productDescription("No full stop", "Breads"), "No full stop. Breads from Djini's Bakehouse.");
  assert.equal(productDescription("", "Breads"), "Breads from Djini's Bakehouse.");
  assert.equal(productDescription(null, null), DEFAULT_DESCRIPTION);
  assert.equal(productDescription("x".repeat(150), "Breads"), `${"x".repeat(150)}.`);
});

test("a shelf names its tab after itself", () => {
  assert.equal(shelfTitle("Breads"), "Breads — Djini's Bakehouse");
});
