/* The quote state machine's own tests — plain node:test, like format.test.js.
   They pin the stale-quote discipline: only the newest basket's answer
   counts, old totals never outlive their basket, and submission waits for a
   settled quote. */

import test from "node:test";
import assert from "node:assert/strict";

import { basketKey, canSubmitWith, initialState, quoteReducer } from "./quote.js";

const settle = (seq, quote) => ({ type: "settled", seq, quote });
const fail = (seq, error) => ({ type: "failed", seq, error });
const refresh = (seq) => ({ type: "refresh", seq });

const A = { total: 10, items: [] };
const B = { total: 20, items: [] };

test("a settled quote is the only state that allows submission", () => {
  const loading = quoteReducer(initialState, refresh(1));
  assert.equal(loading.status, "loading");
  assert.equal(canSubmitWith(loading), false);

  const ready = quoteReducer(loading, settle(1, A));
  assert.equal(ready.status, "ready");
  assert.deepEqual(ready.quote, A);
  assert.equal(canSubmitWith(ready), true);
});

test("an answer for an older basket never overwrites a newer one", () => {
  // Basket 1's request is in flight when the basket changes: the late
  // answer — success or failure — must land nowhere.
  let state = quoteReducer(initialState, refresh(1));
  state = quoteReducer(state, settle(1, A));
  state = quoteReducer(state, refresh(2));
  assert.equal(state.status, "loading");
  assert.deepEqual(state.quote, A, "old totals stay visible while refreshing");

  state = quoteReducer(state, settle(1, B)); // stale, but well-formed
  assert.deepEqual(state.quote, A, "the stale answer is discarded");

  state = quoteReducer(state, fail(1, new Error("late failure")));
  assert.equal(state.status, "loading", "a stale failure is discarded too");

  state = quoteReducer(state, settle(2, B));
  assert.equal(state.status, "ready");
  assert.deepEqual(state.quote, B);
});

test("a failed refresh retires the old totals — stale figures never outlive their basket", () => {
  let state = quoteReducer(initialState, refresh(1));
  state = quoteReducer(state, settle(1, A));
  state = quoteReducer(state, refresh(2));
  state = quoteReducer(state, fail(2, { response: { status: 400 } }));
  assert.equal(state.status, "error");
  assert.equal(state.quote, null);
  assert.equal(canSubmitWith(state), false);
});

test("an emptied basket resets to idle", () => {
  let state = quoteReducer(initialState, refresh(1));
  state = quoteReducer(state, settle(1, A));
  state = quoteReducer(state, { type: "empty" });
  assert.deepEqual(state, initialState);
  assert.equal(canSubmitWith(state), false);
});

test("the basket key ignores line order and catches real changes", () => {
  const items = [
    { product_id: 3, quantity: 2 },
    { product_id: 7, quantity: 1 },
  ];
  assert.equal(
    basketKey(items, "welcome10"),
    basketKey([...items].reverse(), "WELCOME10 "),
  );
  assert.notEqual(basketKey(items, "welcome10"), basketKey(items, ""));
  assert.notEqual(
    basketKey(items),
    basketKey([{ product_id: 3, quantity: 3 }, { product_id: 7, quantity: 1 }]),
  );
});
