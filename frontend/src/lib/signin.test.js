import assert from "node:assert/strict";
import { test } from "node:test";
import { identify, looksLikePhone, saysItsCountry } from "./signin.js";

test("an address is an address, however it is padded", () => {
  assert.deepEqual(identify("  you@example.com "), { email: "you@example.com" });
  assert.deepEqual(identify("a.b+tag@mail.example.org"), { email: "a.b+tag@mail.example.org" });
});

test("a phone number is a phone number in every way people write one", () => {
  for (const typed of ["0176 555 0100", "+49 176 555 0100", "0049-176-5550100", "+49 (176) 555.0100", "01765550100"]) {
    assert.deepEqual(identify(typed), { phone: typed }, typed);
    assert.equal(looksLikePhone(typed), true, typed);
  }
});

test("what is neither is neither", () => {
  for (const typed of ["", "   ", "amara", "12345", "+49", "call me", "0176 abc 0100"]) {
    assert.equal(identify(typed), null, JSON.stringify(typed));
  }
});

test("a half-typed number already reads as a number, an address never does", () => {
  assert.equal(looksLikePhone("017"), true);
  assert.equal(looksLikePhone("01"), false);
  assert.equal(looksLikePhone("you@example.com"), false);
  assert.equal(looksLikePhone("0176@example.com"), false);
});

test("a sign-in number says its country: a leading 0 or a +", () => {
  assert.equal(saysItsCountry("0176 555 0100"), true);
  assert.equal(saysItsCountry(" +49 176 555 0100"), true);
  assert.equal(saysItsCountry("176 555 0100"), false);
  assert.equal(saysItsCountry("49 176 555 0100"), false);
});
