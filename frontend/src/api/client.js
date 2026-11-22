/**
 * Axios instance for the bakehouse API.
 *
 * - Attaches the stored access token to every request.
 * - On a 401, transparently refreshes the token once and retries the
 *   original request; if refreshing fails, storage is cleared and an
 *   `auth:logout` event lets AuthContext drop the user.
 */
import axios from "axios";
import { apiError } from "../lib/apiError";

const TOKEN_KEY = "bakehouse_access";
const REFRESH_KEY = "bakehouse_refresh";

/* The session lives in localStorage so it survives a reload and is the
   same in every tab. A browser can refuse storage outright (blocked site
   data, some private modes) — writes throw. A value that could not be
   stored is kept in memory for as long as the tab lives, instead of taking
   the whole app down with an uncaught error on the first login.

   Memory holds *only* what storage refused. A value that was stored is
   read back from storage every time — so when another tab signs out and
   clears it, this tab finds nothing too, rather than a private copy of a
   session that has ended. */
const memory = new Map();
const store = {
  get(key) {
    if (memory.has(key)) return memory.get(key);
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(key, value);
      memory.delete(key);
    } catch {
      memory.set(key, value);
    }
  },
  remove(key) {
    memory.delete(key);
    try {
      localStorage.removeItem(key);
    } catch {
      /* nothing was stored there */
    }
  },
};

export const tokens = {
  /** The storage key another tab's sign-in or sign-out shows up under. */
  accessKey: TOKEN_KEY,
  get access() {
    return store.get(TOKEN_KEY);
  },
  get refresh() {
    return store.get(REFRESH_KEY);
  },
  save(access, refresh) {
    store.set(TOKEN_KEY, access);
    if (refresh) store.set(REFRESH_KEY, refresh);
  },
  clear() {
    store.remove(TOKEN_KEY);
    store.remove(REFRESH_KEY);
  },
};

/* How long a request may go unanswered before the page stops waiting and
   says so — without a limit, a stalled connection is a spinner that never
   ends. Reads are quick; a write may have Stripe or PayPal on its far side.
   Uploads and downloads are exempt: how long they take is the visitor's
   line, not a sign the shop has stopped answering. A call that knows it
   runs long can pass its own `timeout` — none does today (a campaign is
   sent by a background task; its request answers at once). */
const READ_TIMEOUT_MS = 30_000;
const WRITE_TIMEOUT_MS = 60_000;

const client = axios.create({ baseURL: "/api", timeout: READ_TIMEOUT_MS });

client.interceptors.request.use((config) => {
  if (tokens.access) config.headers.Authorization = `Bearer ${tokens.access}`;
  // Only the default is adjusted — a timeout the caller chose stands.
  if (config.timeout === READ_TIMEOUT_MS) {
    const carriesFile = typeof FormData !== "undefined" && config.data instanceof FormData;
    if (carriesFile || config.responseType === "blob") config.timeout = 0;
    else if ((config.method ?? "get").toLowerCase() !== "get") config.timeout = WRITE_TIMEOUT_MS;
  }
  return config;
});

// A single shared refresh promise, so parallel 401s don't refresh twice.
let refreshing = null;

client.interceptors.response.use(
  (response) => response,
  async (error) => {
    const { response, config } = error;
    // Signing in, signing up and refreshing answer 401 for their own
    // reasons (a wrong password) — never a sign that the session ended.
    const credentialCall = ["/auth/refresh", "/auth/login", "/auth/register"].some((path) =>
      config?.url?.includes(path),
    );
    const canRefresh =
      response?.status === 401 && !config._retried && tokens.refresh && !credentialCall;

    if (canRefresh) {
      config._retried = true;
      try {
        refreshing =
          refreshing ??
          axios.post("/api/auth/refresh", { refresh_token: tokens.refresh });
        const { data } = await refreshing;
        refreshing = null;
        tokens.save(data.access_token);
        config.headers.Authorization = `Bearer ${data.access_token}`;
        return client(config);
      } catch {
        refreshing = null;
        tokens.clear();
        window.dispatchEvent(new Event("auth:logout"));
      }
    } else if (response?.status === 401 && !credentialCall) {
      // Refused, with nothing left to refresh the session with — it ended
      // somewhere else (signed out in another tab, storage cleared). Say
      // so, rather than leave the page looking signed in while every
      // request it makes fails. Harmless when nobody was signed in.
      tokens.clear();
      window.dispatchEvent(new Event("auth:logout"));
    }
    return Promise.reject(error);
  }
);

// Lives in lib/ (no imports of its own, so it is unit-tested there); every
// page keeps importing it from here.
export { apiError };

/** True when the server paused pricing itself — quotes and placement
    refuse with 503 while the delivery rules await owner approval as EUR
    figures. The server's message already tells the customer what's wrong
    ("Checkout is paused — …"), so callers show it as-is instead of
    appending "check your basket" advice that would point the finger at
    the wrong thing. */
export function checkoutPaused(error) {
  return apiError(error, "").toLowerCase().includes("paused");
}

export default client;
