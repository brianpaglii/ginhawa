import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "../components/Toast";
import { useToast } from "../components/use-toast";

function Trigger({
  variant,
  title,
  message,
}: {
  variant: "error" | "success" | "info";
  title: string;
  message?: string;
}) {
  const toast = useToast();
  return (
    <button onClick={() => toast[variant]({ title, message })}>
      fire {variant}
    </button>
  );
}

describe("ToastProvider + useToast", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  // Verifies the happy path: clicking the trigger shows a toast
  // with the title and message visible. Mortality: would fail if
  // the provider's setToasts state update regressed, or if the
  // toast markup changed in a way that hid the body.
  it("displays a toast with title + message", () => {
    render(
      <ToastProvider>
        <Trigger variant="success" title="Saved" message="All good." />
      </ToastProvider>,
    );
    act(() => {
      screen.getByText("fire success").click();
    });
    expect(screen.getByText("Saved")).toBeInTheDocument();
    expect(screen.getByText("All good.")).toBeInTheDocument();
  });

  // Verifies the auto-dismiss timer. After advancing past the
  // configured timeout (we use a custom 200 ms here for speed),
  // the toast unmounts.
  // Mortality: would fail if the per-toast useEffect timer regressed
  // or if dismiss() weren't wired through to setToasts.
  it("auto-dismisses after the configured timeout", () => {
    render(
      <ToastProvider autoDismissMs={200}>
        <Trigger variant="error" title="Oops" />
      </ToastProvider>,
    );
    act(() => {
      screen.getByText("fire error").click();
    });
    expect(screen.getByText("Oops")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(screen.queryByText("Oops")).not.toBeInTheDocument();
  });

  // Verifies multiple toasts can stack — firing twice in quick
  // succession produces two visible toasts at the same time. Per-
  // toast dismissal timing is covered by the previous test; this
  // one only proves that the second toast doesn't replace the
  // first.
  it("stacks multiple toasts on top of each other", () => {
    render(
      <ToastProvider autoDismissMs={300}>
        <Trigger variant="info" title="Stacked" />
      </ToastProvider>,
    );
    act(() => {
      screen.getByText("fire info").click();
    });
    act(() => {
      screen.getByText("fire info").click();
    });
    expect(screen.getAllByText("Stacked")).toHaveLength(2);
  });
});
