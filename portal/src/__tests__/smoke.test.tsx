import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { AuthContext, type AuthContextValue } from "../auth/AuthContext";
import { DashboardPage } from "../pages/DashboardPage";
import { LoginPage } from "../pages/LoginPage";
import type { UserRead } from "../api/client";

function makeAuth(overrides: Partial<AuthContextValue> = {}): AuthContextValue {
  return {
    user: null,
    status: "unauthenticated",
    login: async () => undefined,
    logout: async () => undefined,
    ...overrides,
  };
}

describe("smoke", () => {
  it("renders the LoginPage without crashing", () => {
    render(
      <AuthContext.Provider value={makeAuth()}>
        <MemoryRouter>
          <LoginPage />
        </MemoryRouter>
      </AuthContext.Provider>,
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "GINHAWA" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Username")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  it("greets the authenticated user on the dashboard", () => {
    const user: UserRead = {
      id: "user-1",
      username: "bhw_tibagan",
      full_name: "Maria Tibagan",
      role: "bhw",
      assigned_barangay: "Tibagan",
      is_active: 1,
      created_at: "2026-01-01T00:00:00+00:00",
      last_login_at: null,
    };
    render(
      <AuthContext.Provider value={makeAuth({ user, status: "authenticated" })}>
        <MemoryRouter>
          <DashboardPage />
        </MemoryRouter>
      </AuthContext.Provider>,
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "Hello, Maria Tibagan." }),
    ).toBeInTheDocument();
  });

  it("falls back to username when full_name is empty", () => {
    const user: UserRead = {
      id: "user-2",
      username: "admin",
      full_name: "",
      role: "admin",
      assigned_barangay: null,
      is_active: 1,
      created_at: "2026-01-01T00:00:00+00:00",
      last_login_at: null,
    };
    render(
      <AuthContext.Provider value={makeAuth({ user, status: "authenticated" })}>
        <MemoryRouter>
          <DashboardPage />
        </MemoryRouter>
      </AuthContext.Provider>,
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "Hello, admin." }),
    ).toBeInTheDocument();
  });
});
