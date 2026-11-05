/**
 * Axios instance for the bakehouse API.
 *
 * - Attaches the stored access token to every request.
 * - On a 401, transparently refreshes the token once and retries the
 *   original request; if refreshing fails, storage is cleared and an
 *   `auth:logout` event lets AuthContext drop the user.
 */
import axios from "axios";

const TOKEN_KEY = "bakehouse_access";
const REFRESH_KEY = "bakehouse_refresh";

export const tokens = {
  get access() {
    return localStorage.getItem(TOKEN_KEY);
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY);
  },
  save(access, refresh) {
    localStorage.setItem(TOKEN_KEY, access);
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};

const client = axios.create({ baseURL: "/api" });

client.interceptors.request.use((config) => {
  if (tokens.access) config.headers.Authorization = `Bearer ${tokens.access}`;
  return config;
});

// A single shared refresh promise, so parallel 401s don't refresh twice.
let refreshing = null;

client.interceptors.response.use(
  (response) => response,
  async (error) => {
    const { response, config } = error;
    const canRefresh =
      response?.status === 401 &&
      !config._retried &&
      tokens.refresh &&
      !config.url.includes("/auth/refresh") &&
      !config.url.includes("/auth/login") &&
      !config.url.includes("/auth/register");

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
    }
    return Promise.reject(error);
  }
);

/** Pull a human-readable message out of a FastAPI error response. */
export function apiError(error, fallback = "Something went wrong") {
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail[0]?.msg) {
    const field = detail[0]?.loc?.at(-1);
    return field ? `${field}: ${detail[0].msg}` : detail[0].msg;
  }
  return error?.message ?? fallback;
}

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
