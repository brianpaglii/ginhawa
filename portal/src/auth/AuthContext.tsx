import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { ApiError, apiClient, readToken, type UserRead } from "../api/client";

export type AuthStatus = "unauthenticated" | "authenticating" | "authenticated";

export interface AuthContextValue {
  user: UserRead | null;
  status: AuthStatus;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

// Exported for tests: tests render <AuthContext.Provider value={...}> with
// a mocked value rather than spinning up the real provider's effects.
export const AuthContext = createContext<AuthContextValue | null>(null);

interface AuthProviderProps {
  children: ReactNode;
}

export function AuthProvider({ children }: AuthProviderProps) {
  // If we already have a token at boot, treat the app as "authenticating"
  // until /users/me confirms (or fails). Skipping this would flash the
  // login screen for one render on every refresh.
  const [user, setUser] = useState<UserRead | null>(null);
  const [status, setStatus] = useState<AuthStatus>(() =>
    readToken() ? "authenticating" : "unauthenticated",
  );

  // Hydrate the user from the stored token on mount. A 401 here means
  // the token is dead (expired, user deactivated) — clear it and drop
  // back to unauthenticated.
  useEffect(() => {
    let cancelled = false;
    if (status !== "authenticating") return;

    apiClient
      .getMe()
      .then((me) => {
        if (cancelled) return;
        setUser(me);
        setStatus("authenticated");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) {
          // Stored token is dead.
          void apiClient.logout().catch(() => undefined);
        }
        setUser(null);
        setStatus("unauthenticated");
      });

    return () => {
      cancelled = true;
    };
    // We only run this once on mount; status === "authenticating" only
    // at boot when there's a stored token.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    setStatus("authenticating");
    try {
      await apiClient.login({ username, password });
      const me = await apiClient.getMe();
      setUser(me);
      setStatus("authenticated");
    } catch (err) {
      setUser(null);
      setStatus("unauthenticated");
      throw err;
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiClient.logout();
    } finally {
      setUser(null);
      setStatus("unauthenticated");
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ user, status, login, logout }),
    [user, status, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (ctx === null) {
    throw new Error("useAuth must be used within an <AuthProvider>");
  }
  return ctx;
}
