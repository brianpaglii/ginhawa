// Context object + value type. Kept in a non-component file so that
// react-refresh's "only-export-components" rule stays happy in
// AuthContext.tsx (which now exports just the provider component).
//
// Tests can import AuthContext from here to inject a fake value:
//
//   <AuthContext.Provider value={fakeValue}>...

import { createContext } from "react";

import type { UserRead } from "../api/client";

export type AuthStatus = "unauthenticated" | "authenticating" | "authenticated";

export interface AuthContextValue {
  user: UserRead | null;
  status: AuthStatus;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);
