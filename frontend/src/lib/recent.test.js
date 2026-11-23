/* The "recently viewed" list — plain node:test (npm test). */

import test from "node:test";
import assert from "node:assert/strict";

import { MAX_RECENT, withBake } from "./recent.js";

const bake = (slug, extra = {}) => ({ slug, name: slug.toUpperCase(), image_url: `/static/${slug}.jpg`, ...extra });

test("the newest look goes to the front, once", () => {
  let list = withBake([], bake("croissant"));
  list = withBake(list, bake("rye"));
  list = withBake(list, bake("croissant"));
  assert.deepEqual(list.map((b) => b.slug), ["croissant", "rye"]);
});

test("only what a tile draws is kept — never a price or a stock count", () => {
  const [kept] = withBake([], bake("rye", { current_price: 4.5, stock: 3, description: "dense" }));
  assert.deepEqual(kept, { slug: "rye", name: "RYE", image_url: "/static/rye.jpg" });
});

test("the list never outgrows its shelf", () => {
  let list = [];
  for (let i = 0; i < MAX_RECENT + 5; i += 1) list = withBake(list, bake(`bake-${i}`));
  assert.equal(list.length, MAX_RECENT);
  assert.equal(list[0].slug, `bake-${MAX_RECENT + 4}`);
});

test("whatever storage hands back is tidied, not trusted", () => {
  const mangled = [null, 7, { name: "no slug" }, { slug: "ok", name: 12, image_url: { evil: true } }, { slug: "fine" }];
  assert.deepEqual(withBake(mangled, null), [
    { slug: "ok", name: "", image_url: null },
    { slug: "fine", name: "", image_url: null },
  ]);
  assert.deepEqual(withBake("not a list", null), []);
  assert.deepEqual(withBake(undefined, { name: "no slug either" }), []);
});
