import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { ErrorBoundary } from "../components/ErrorBoundary";

function Boom(): never {
  throw new Error("kaboom");
}

describe("ErrorBoundary", () => {
  // Silence the deliberate console.error from React + the boundary
  // itself; we only care about the user-visible fallback. Restore
  // the real implementation between tests so unrelated diagnostics
  // still surface.
  let consoleSpy: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    consoleSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
  });
  afterEach(() => {
    consoleSpy.mockRestore();
  });

  // Verifies the fallback renders for a child that throws during
  // render. Mortality: would fail if getDerivedStateFromError were
  // dropped, or if the fallback markup regressed.
  it("renders the fallback UI when a child throws", () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "Something went wrong" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
  });

  // Verifies that "Try again" clears the boundary's error state. We
  // start with the throwing child, then swap in a healthy one and
  // click reset; the fallback should disappear and the healthy
  // child should mount.
  it("recovers when 'Try again' is clicked and the child stops throwing", () => {
    function Toggle({ shouldThrow }: { shouldThrow: boolean }) {
      if (shouldThrow) throw new Error("transient");
      return <p>Recovered content</p>;
    }
    const { rerender } = render(
      <ErrorBoundary>
        <Toggle shouldThrow={true} />
      </ErrorBoundary>,
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "Something went wrong" }),
    ).toBeInTheDocument();

    rerender(
      <ErrorBoundary>
        <Toggle shouldThrow={false} />
      </ErrorBoundary>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(screen.getByText("Recovered content")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Something went wrong" }),
    ).not.toBeInTheDocument();
  });

  // Verifies the technical-detail disclosure exists and contains the
  // thrown message — useful context for someone debugging in the
  // browser console + UI together.
  it("includes the error message in the technical-details disclosure", () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByText(/kaboom/)).toBeInTheDocument();
  });
});
