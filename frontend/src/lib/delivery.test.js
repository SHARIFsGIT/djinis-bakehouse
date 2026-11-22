/* The delivery-area mirror — plain node:test (npm test). The server's own
   rules are pinned in backend/tests/test_hardening.py; these keep the
   storefront's early warning in step with them. */

import test from "node:test";
import assert from "node:assert/strict";

import { deliversTo, describeArea } from "./delivery.js";

const AREA = ["10115", "101*", "12043", "SW1A*"];

test("a postal code is inside the area by exact match or by area prefix", () => {
  for (const inside of ["10115", " 10119 ", "12043", "12 043", "sw1a 1aa"]) {
    assert.equal(deliversTo(inside, AREA), true, inside);
  }
  for (const outside of ["80331", "1001", "12044", "", null, undefined]) {
    assert.equal(deliversTo(outside, AREA), false, String(outside));
  }
});

test("no list means no limit", () => {
  assert.equal(deliversTo("80331", []), true);
  assert.equal(deliversTo("", undefined), true);
});

test("the area reads as a short line however long the list is", () => {
  assert.equal(describeArea(["10115", "101*"]), "10115, 101…");
  const long = Array.from({ length: 20 }, (_, i) => String(10100 + i));
  assert.equal(describeArea(long, 3), "10100, 10101, 10102 and 17 more");
  assert.equal(describeArea([]), "");
});
