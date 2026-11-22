/* What a failed request says to the person at the page — plain node:test
   (npm test). The errors below are shaped the way axios shapes them. */

import test from "node:test";
import assert from "node:assert/strict";

import { UNREACHABLE, apiError } from "./apiError.js";

const refused = (status, data) => ({
  isAxiosError: true,
  message: `Request failed with status code ${status}`,
  response: { status, data },
});

test("the shop's own answer is shown as it is", () => {
  assert.equal(
    apiError(refused(400, { detail: "Only 2 left of Seeded Rye" }), "Couldn't place the order"),
    "Only 2 left of Seeded Rye",
  );
});

test("a validation refusal names the field and what is wrong with it", () => {
  const error = refused(422, {
    detail: [{ loc: ["body", "email"], msg: "value is not a valid email address" }],
  });
  assert.equal(apiError(error), "email: value is not a valid email address");
  // ...and a refusal of the body as a whole is just its message
  const whole = refused(422, { detail: [{ loc: ["body"], msg: "Give exactly one discount" }] });
  assert.equal(apiError(whole), "Give exactly one discount");
});

test("no answer at all is said plainly — never the library's own words", () => {
  for (const code of ["ERR_NETWORK", "ECONNABORTED", "ETIMEDOUT"]) {
    const error = { isAxiosError: true, code, message: "Network Error" };
    assert.equal(apiError(error, "Couldn't place the order"), UNREACHABLE, code);
  }
});

test("an answer with nothing to say falls back to the caller's words", () => {
  assert.equal(apiError(refused(500, "Internal Server Error"), "Couldn't place the order"), "Couldn't place the order");
  assert.equal(apiError(refused(502, "<html>Bad gateway</html>")), "Something went wrong");
  assert.equal(apiError(refused(500, { detail: "" }), "Couldn't save"), "Couldn't save");
  assert.equal(apiError(refused(500, { detail: { code: 7 } }), "Couldn't save"), "Couldn't save");
});

test("a request the page cancelled itself is not a connection problem", () => {
  const error = { isAxiosError: true, code: "ERR_CANCELED", message: "canceled" };
  assert.equal(apiError(error, "Stopped"), "Stopped");
});

test("an error of the page's own making keeps its own words", () => {
  assert.equal(apiError(new Error("That file isn't an image"), "Upload failed"), "That file isn't an image");
  assert.equal(apiError(null, "Upload failed"), "Upload failed");
  assert.equal(apiError(undefined), "Something went wrong");
});
