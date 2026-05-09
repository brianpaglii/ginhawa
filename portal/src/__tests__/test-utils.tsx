// Shared render helper for component tests.
//
// Wraps the unit under test in the providers it needs in production
// (QueryClient, Router, AuthContext) but with test-friendly defaults
// (retry:false so 500 surfaces immediately, MemoryRouter so navigate
// calls don't try to mutate jsdom history globally).

import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AuthContext, type AuthContextValue } from "../auth/auth-context";
import { ToastProvider } from "../components/Toast";
import type { UserRead } from "../api/client";

export const FAKE_BHW_USER: UserRead = {
  id: "user-1",
  username: "bhw_tibagan",
  full_name: "Maria Tibagan",
  role: "bhw",
  assigned_barangay: "Tibagan",
  is_active: 1,
  created_at: "2026-01-01T00:00:00+00:00",
  last_login_at: null,
};

export function makeAuth(
  overrides: Partial<AuthContextValue> = {},
): AuthContextValue {
  return {
    user: null,
    status: "unauthenticated",
    login: async () => undefined,
    logout: async () => undefined,
    ...overrides,
  };
}

interface Options {
  auth?: AuthContextValue;
  initialEntries?: string[];
}

export function renderWithProviders(
  ui: ReactElement,
  { auth, initialEntries = ["/"] }: Options = {},
) {
  // Fresh QueryClient per render so cached data from one test never
  // leaks into the next. retry:false makes errors visible without
  // waiting through react-query's default backoff.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const authValue =
    auth ?? makeAuth({ user: FAKE_BHW_USER, status: "authenticated" });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <AuthContext.Provider value={authValue}>
          <MemoryRouter initialEntries={initialEntries}>{ui}</MemoryRouter>
        </AuthContext.Provider>
      </ToastProvider>
    </QueryClientProvider>,
  );
}
