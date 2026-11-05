/* The costing desk's vocabulary, pinned from both sides: every status the
   backend can send has a label and a badge, and the arithmetic helpers
   refuse to guess. Plain node:test like the rest of src/lib:
     node --test src/lib/costing.test.js   (or: npm test) */

import test from "node:test";
import assert from "node:assert/strict";

import {
  COST_STATUSES,
  REVIEW_REASONS,
  UNIT_OPTIONS,
  costStatusMeta,
  overheadRatePerHour,
} from "./costing.js";

test("every derived cost status has a distinct label and badge", () => {
  const badges = new Set();
  for (const meta of Object.values(COST_STATUSES)) {
    assert.ok(meta.label.length > 0);
    assert.ok(meta.badge.startsWith("badge-"));
    badges.add(meta.badge);
  }
  // Two statuses may share a colour family, but never a whole badge+label pair.
  assert.equal(
    new Set(Object.values(COST_STATUSES).map((m) => m.label)).size,
    Object.keys(COST_STATUSES).length,
  );
  assert.ok(badges.size >= 4);
});

test("an unknown or missing status reads as itself, never as a real one", () => {
  assert.equal(costStatusMeta("costed").label, "Costed");
  assert.equal(costStatusMeta("weird").label, "weird");
  assert.equal(costStatusMeta(undefined).label, "unknown");
  assert.equal(costStatusMeta(null).badge, "badge-ghost");
});

test("the review reasons cover the backend's four, all human words", () => {
  for (const key of ["no_cost_record", "incomplete", "inputs_overdue", "scheduled"]) {
    assert.ok(REVIEW_REASONS[key].length > 0);
  }
});

test("overhead rate per hour divides honestly and never invents a figure", () => {
  assert.equal(overheadRatePerHour(800, 160), 5);
  assert.equal(overheadRatePerHour("37.5", "25"), 1.5); // strings from inputs
  assert.equal(overheadRatePerHour(0, 160), 0);
  assert.equal(overheadRatePerHour(800, 0), null); // the guard the backend raises on
  assert.equal(overheadRatePerHour(800, -10), null);
  assert.equal(overheadRatePerHour("", 160), null);
  assert.equal(overheadRatePerHour(800, ""), null);
  assert.equal(overheadRatePerHour(null, null), null);
});

test("the unit select speaks exactly the backend's canonical vocabulary", () => {
  assert.deepEqual(UNIT_OPTIONS, ["g", "kg", "ml", "l", "each", "mg"]);
});
