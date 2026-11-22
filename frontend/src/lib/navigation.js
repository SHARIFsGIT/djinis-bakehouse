/* Where to go once someone has signed in: back to the page that sent them
   to the login door. The page names itself in router state (the route
   guards and the "log in to…" links do), or on the address as ?from=.

   Only ever a path inside this shop. An address can be written by anyone
   — "/login?from=//elsewhere.example" in an email — and the router hands a
   path it cannot push to the browser as a full navigation, which would
   make the login page a way to send a customer anywhere. So anything that
   is not a plain same-site path is answered with the home page. */

const isOwnPath = (value) =>
  typeof value === "string" &&
  value.startsWith("/") &&
  !value.startsWith("//") &&
  !value.includes("\\") &&
  // no control characters (a tab or newline inside a URL is stripped by the
  // browser, which can turn "/\t/host" into "//host")
  !/[\u0000-\u001f\u007f]/.test(value);

/** The path to return to after login, from a router `location`. */
export const returnPath = (location, fallback = "/") => {
  const fromState = location?.state?.from;
  if (isOwnPath(fromState)) return fromState;
  let fromQuery = null;
  try {
    fromQuery = new URLSearchParams(location?.search ?? "").get("from");
  } catch {
    fromQuery = null;
  }
  return isOwnPath(fromQuery) ? fromQuery : fallback;
};
