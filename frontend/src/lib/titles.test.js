/* The browser-tab titles — plain node:test (npm test). */

import test from "node:test";
import assert from "node:assert/strict";

import { DEFAULT_TITLE, titleFor } from "./titles.js";

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
