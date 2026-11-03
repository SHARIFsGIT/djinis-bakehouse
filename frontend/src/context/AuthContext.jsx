import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import client, { tokens } from "../api/client";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(false);

  // Bootstrap: if we hold a token, fetch the user it belongs to.
  useEffect(() => {
    let cancelled = false;
    if (!tokens.access) {
      setReady(true);
      return;
    }
    client
      .get("/auth/me")
      .then(({ data }) => !cancelled && setUser(data))
      .catch(() => {})
      .finally(() => !cancelled && setReady(true));
    return () => {
      cancelled = true;
    };
  }, []);

  // The API client clears tokens and fires this when a refresh fails.
  useEffect(() => {
    const onForcedLogout = () => setUser(null);
    window.addEventListener("auth:logout", onForcedLogout);
    return () => window.removeEventListener("auth:logout", onForcedLogout);
  }, []);

  const applyTokens = useCallback((data) => {
    tokens.save(data.access_token, data.refresh_token);
    setUser(data.user);
    return data.user;
  }, []);

  const login = useCallback(
    async (email, password) => applyTokens((await client.post("/auth/login", { email, password })).data),
    [applyTokens]
  );

  // Redeem a one-tap sign-in link (the "Email me a sign-in link" flow).
  const loginWithToken = useCallback(
    async (token) => applyTokens((await client.post("/auth/quick-login/redeem", { token })).data),
    [applyTokens]
  );

  const register = useCallback(
    async (payload) => applyTokens((await client.post("/auth/register", payload)).data),
    [applyTokens]
  );

  const logout = useCallback(() => {
    tokens.clear();
    setUser(null);
  }, []);

  const refreshUser = useCallback(async () => {
    const { data } = await client.get("/auth/me");
    setUser(data);
    return data;
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        ready,
        isAdmin: user?.role === "admin",
        login,
        loginWithToken,
        register,
        logout,
        refreshUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
