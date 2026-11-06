/* The Promotions page's redemption dropdown, pinned from the side both the
   create form and the coupon table lean on: the coupon model's single rule
   field (once_per_customer) and the option values the selects speak.
   Plain node:test like the rest of src/lib:
     node --test src/lib/redemptionRules.test.js   (or: npm test) */

import test from "node:test";
import assert from "node:assert/strict";

import {
  REDEMPTION_RULES,
  oncePerCustomerFromRule,
  redemptionRule,
} from "./redemptionRules.js";

test("the rule vocabulary covers the model exactly — both booleans, nothing invented", () => {
  assert.deepEqual(
    REDEMPTION_RULES.map((r) => r.once_per_customer),
    [false, true],
  );
  // Distinct option values, or the selects couldn't tell the rules apart.
  assert.equal(new Set(REDEMPTION_RULES.map((r) => r.value)).size, REDEMPTION_RULES.length);
  for (const rule of REDEMPTION_RULES) {
    assert.ok(rule.label.length > 0);
  }
});

test("model → dropdown → model round-trips without drifting", () => {
  for (const once of [false, true]) {
    assert.equal(oncePerCustomerFromRule(redemptionRule(once)), once);
  }
  for (const rule of REDEMPTION_RULES) {
    assert.equal(redemptionRule(oncePerCustomerFromRule(rule.value)), rule.value);
  }
});

test("each option value pins the boolean it must send the backend", () => {
  assert.equal(oncePerCustomerFromRule("repeatable"), false);
  assert.equal(oncePerCustomerFromRule("once"), true);
});

test("an unknown or stale option falls back to the backend's default: repeatable", () => {
  // A corrupted or future value must never quietly flip a coupon into
  // 1-per-customer; it reads as the model's own default.
  assert.equal(oncePerCustomerFromRule("forever"), false);
  assert.equal(oncePerCustomerFromRule(""), false);
  assert.equal(oncePerCustomerFromRule(undefined), false);
});
