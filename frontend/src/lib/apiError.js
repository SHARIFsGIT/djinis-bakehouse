/* Turning a failed request into words for the person looking at the page.

   Three sources, in order of authority:
     1. The shop's own answer — the API's `detail` ("Only 2 left of Seeded
        Rye"). Always shown as it is.
     2. No answer at all — offline, a dropped connection, a request that
        timed out. The page says exactly that.
     3. An answer with nothing to say — a bare 500 or a proxy's 502 page.
        The caller's own fallback ("Couldn't place the order").

   What is never shown is the HTTP library's text for 2 and 3 ("Network
   Error", "Request failed with status code 502"): that is written for
   whoever reads the console, not for a customer at the checkout. */

export const UNREACHABLE =
  "Couldn't reach the shop — check your connection and try again.";

/** Pull a human-readable message out of a failed API call. */
export function apiError(error, fallback = "Something went wrong") {
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  // A validation refusal (422) arrives as a list — the first entry names
  // the field and what is wrong with it.
  if (Array.isArray(detail) && detail[0]?.msg) {
    const field = detail[0]?.loc?.at(-1);
    return field && field !== "body" ? `${field}: ${detail[0].msg}` : detail[0].msg;
  }
  // An error of the page's own making (an image that couldn't be read, a
  // file that is too big) carries its own words.
  if (!error?.isAxiosError) return error?.message || fallback;
  // A request this page cancelled itself is not a connection problem.
  if (!error.response && error.code !== "ERR_CANCELED") return UNREACHABLE;
  return fallback;
}
