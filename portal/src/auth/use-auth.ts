// useAuth() lives in its own module so AuthContext.tsx can satisfy
// the react-refresh "only-export-components" rule.

import { useContext } from "react";

import { AuthContext, type AuthContextValue } from "./auth-context";

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (ctx === null) {
    throw new Error("useAuth must be used within an <AuthProvider>");
  }
  return ctx;
}
