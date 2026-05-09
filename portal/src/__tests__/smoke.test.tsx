import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LoginPage } from "../pages/LoginPage";
import { renderWithProviders, makeAuth } from "./test-utils";

describe("smoke", () => {
  it("renders the LoginPage without crashing", () => {
    renderWithProviders(<LoginPage />, { auth: makeAuth() });
    expect(
      screen.getByRole("heading", { level: 1, name: "GINHAWA" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Username")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    // Submit starts disabled (both fields blank) and only enables when
    // the user has typed something into both — guards against
    // accidental enter-key submits with empty creds.
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
  });
});
