/* Where the login page sends someone afterwards — plain node:test
   (npm test). The rule under test: a path inside the shop, or home. */

import test from "node:test";
import assert from "node:assert/strict";

import { returnPath } from "./navigation.js";

test("the page that sent the visitor to the login door gets them back", () => {
  assert.equal(returnPath({ state: { from: "/checkout" }, search: "" }), "/checkout");
  assert.equal(
    returnPath({ state: { from: "/account/orders?status=pending" }, search: "" }),
    "/account/orders?status=pending",
  );
  // ?from= on the address works too, and router state wins when both exist
  assert.equal(returnPath({ state: null, search: "?from=/checkout" }), "/checkout");
  assert.equal(returnPath({ state: { from: "/cart" }, search: "?from=/checkout" }), "/cart");
});

test("with nowhere named, it is the home page (or the caller's own fallback)", () => {
  assert.equal(returnPath({ state: null, search: "" }), "/");
  assert.equal(returnPath({}), "/");
  assert.equal(returnPath(undefined), "/");
  assert.equal(returnPath({ state: null, search: "" }, "/account"), "/account");
});

test("an address that leads off the shop is never followed", () => {
  const elsewhere = [
    "//evil.example",
    "//evil.example/checkout",
    "https://evil.example",
    "http://evil.example/",
    "javascript:alert(1)",
    "/\\evil.example",
    "\\\\evil.example",
    "/\t/evil.example",
    "/\n/evil.example",
    "checkout",
    "",
  ];
  for (const target of elsewhere) {
    assert.equal(returnPath({ state: null, search: `?from=${encodeURIComponent(target)}` }), "/", target);
    assert.equal(returnPath({ state: { from: target }, search: "" }), "/", target);
  }
  // Not a string at all (state is whatever a caller put there)
  assert.equal(returnPath({ state: { from: { pathname: "/checkout" } }, search: "" }), "/");
  assert.equal(returnPath({ state: { from: 42 }, search: "" }), "/");
});
