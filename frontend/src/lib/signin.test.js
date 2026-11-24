import assert from "node:assert/strict";
import { test } from "node:test";
import {
  WAY_KEY,
  identify,
  identifyAs,
  initialWay,
  looksLikePhone,
  rememberWay,
  saysItsCountry,
  wayProblem,
} from "./signin.js";

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

test("each tab takes only its own kind of answer", () => {
  assert.deepEqual(identifyAs("email", " you@example.com "), { email: "you@example.com" });
  assert.deepEqual(identifyAs("phone", "0176 555 0100"), { phone: "0176 555 0100" });
  assert.equal(identifyAs("email", "0176 555 0100"), null); // a number under Email
  assert.equal(identifyAs("phone", "you@example.com"), null); // an address under Phone
  assert.equal(identifyAs("phone", "0176 55"), null); // too short to be a number
  assert.equal(identifyAs("email", ""), null);
});

test("a typing that fits the other tab says so", () => {
  assert.match(wayProblem("email", "0176 555 0100"), /Phone tab/);
  assert.match(wayProblem("phone", "you@example.com"), /Email tab/);
  assert.equal(wayProblem("email", "amara"), "Enter the email address on your account.");
  assert.equal(wayProblem("phone", "12", { signingUp: true }), "Enter your phone number — you'll log in with it.");
});

test("the pages open on the tab asked for, else the one last used, else Email", () => {
  const store = new Map();
  globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
  };
  assert.equal(initialWay(""), "email");
  rememberWay("phone");
  assert.equal(store.get(WAY_KEY), "phone");
  assert.equal(initialWay(""), "phone");
  assert.equal(initialWay("?with=email"), "email"); // the address wins
  assert.equal(initialWay("?with=fax"), "phone"); // nonsense is ignored
  // Storage that throws (private mode) never breaks the page.
  globalThis.localStorage = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("denied"); } };
  assert.equal(initialWay(""), "email");
  rememberWay("phone");
  delete globalThis.localStorage;
});
