import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, onUnauthorized } from "./api.js";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    onUnauthorized(() => {
      setUser(null);
      setNotice("Your session ended. Please sign in again.");
    });
    api
      .get("/auth/me")
      .then((res) => setUser(res.user))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.post("/auth/logout");
    } finally {
      setUser(null);
      setNotice("");
    }
  }, []);

  const value = useMemo(
    () => ({ user, setUser, loading, signOut, notice, isAdmin: user?.role === "admin" }),
    [user, loading, signOut, notice],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}
