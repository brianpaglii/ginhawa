import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { Route, Routes } from "react-router-dom";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import { SessionsPage } from "../pages/SessionsPage";
import type { CitizenRead, Page, SessionRead } from "../api/client";
import { renderWithProviders } from "./test-utils";

const API = "http://127.0.0.1:8000";

function makeSession(overrides: Partial<SessionRead>): SessionRead {
  return {
    id: "session-id",
    citizen_id: "citizen-id",
    device_id: "kiosk-1",
    started_at: "2026-05-06T10:00:00+00:00",
    ended_at: "2026-05-06T10:08:00+00:00",
    status: "completed",
    error_reason: null,
    measurement_path: "vitals",
    printed_status: "printed_ok",
    synced: 1,
    updated_at: "2026-05-06T10:08:00+00:00",
    measurement_count: 0,
    ...overrides,
  };
}

function makeCitizen(overrides: Partial<CitizenRead>): CitizenRead {
  return {
    id: "citizen-id",
    rfid_uid: "RFID-AAA",
    full_name: "Citizen Name",
    dob: "1990-01-01",
    sex: "M",
    barangay: "Tibagan",
    phone: null,
    consent_version: "v1",
    consent_given_at: "2026-01-01T00:00:00+00:00",
    registered_at: "2026-01-01T00:00:00+00:00",
    registered_by: null,
    is_active: 1,
    synced: 1,
    updated_at: "2026-01-01T00:00:00+00:00",
    ...overrides,
  };
}

const FAKE_CITIZENS: CitizenRead[] = [
  makeCitizen({ id: "c-1", full_name: "Alice Reyes", rfid_uid: "RFID-ALICE" }),
  makeCitizen({ id: "c-2", full_name: "Ben Cruz", rfid_uid: "RFID-BEN" }),
  makeCitizen({
    id: "c-3",
    full_name: "Maria Dela Cruz",
    rfid_uid: "RFID-MARIA",
  }),
];

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderRoute(initialPath: string = "/sessions") {
  return renderWithProviders(
    <Routes>
      <Route path="/sessions" element={<SessionsPage />} />
    </Routes>,
    { initialEntries: [initialPath] },
  );
}

describe("SessionsPage", () => {
  // Verifies the happy path: three sessions render in the order the
  // server returned them. Citizen names look up correctly via the
  // citizens map built from the second query.
  it("renders mocked sessions with citizen names, newest first", async () => {
    const sessions: Page<SessionRead> = {
      total: 3,
      items: [
        makeSession({
          id: "s-1",
          citizen_id: "c-1",
          started_at: "2026-05-06T19:00:00+00:00",
          status: "in_progress",
          measurement_path: "full",
          measurement_count: 5,
        }),
        makeSession({
          id: "s-2",
          citizen_id: "c-2",
          started_at: "2026-05-06T15:30:00+00:00",
          status: "completed",
          measurement_path: "vitals",
          measurement_count: 3,
        }),
        makeSession({
          id: "s-3",
          citizen_id: "c-1",
          started_at: "2026-05-06T08:15:00+00:00",
          status: "aborted",
          measurement_path: "anthropometric",
          measurement_count: 1,
        }),
      ],
    };
    server.use(
      http.get(`${API}/api/v1/sessions`, () => HttpResponse.json(sessions)),
      http.get(`${API}/api/v1/citizens`, () =>
        HttpResponse.json({ items: FAKE_CITIZENS, total: 3 }),
      ),
    );

    renderRoute();

    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row");
    // 1 header + 3 data rows
    expect(rows).toHaveLength(4);
    expect(within(rows[1]).getByText("Alice Reyes")).toBeInTheDocument();
    expect(within(rows[1]).getByText("In progress")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Ben Cruz")).toBeInTheDocument();
    expect(within(rows[3]).getByText("Aborted")).toBeInTheDocument();

    // Top-of-table count copy + bottom pagination.
    expect(screen.getByText("Showing 1–3 of 3 results")).toBeInTheDocument();
    expect(screen.getByText("Showing 1–3 of 3")).toBeInTheDocument();
  });

  // Verifies the empty state on a fresh load (no user-set filters).
  // The default last-7-days date range still applies, but to the
  // user it reads as "we haven't seen sessions yet" since they
  // haven't touched the filter panel.
  it("renders the empty-default state when no sessions exist", async () => {
    server.use(
      http.get(`${API}/api/v1/sessions`, () =>
        HttpResponse.json({ items: [], total: 0 }),
      ),
      http.get(`${API}/api/v1/citizens`, () =>
        HttpResponse.json({ items: [], total: 0 }),
      ),
    );

    renderRoute();

    expect(
      await screen.findByRole("heading", { level: 2, name: "No sessions yet" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  // Verifies the error state when the API returns 500.
  it("renders an error message when the sessions API returns 500", async () => {
    server.use(
      http.get(`${API}/api/v1/sessions`, () =>
        HttpResponse.json(
          { detail: "internal server error" },
          { status: 500, statusText: "Internal Server Error" },
        ),
      ),
      http.get(`${API}/api/v1/citizens`, () =>
        HttpResponse.json({ items: [], total: 0 }),
      ),
    );

    renderRoute();

    const alert = await waitFor(() => screen.getByRole("alert"));
    expect(alert).toHaveTextContent(/internal server error|request failed/i);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  // Verifies the status filter forwards to the API and updates the
  // URL. Mortality: would fail if useSearchParams wiring regressed
  // or the filter wasn't included in queryParams.
  it("forwards status filter to the API and updates the URL", async () => {
    let lastUrl = "";
    server.use(
      http.get(`${API}/api/v1/sessions`, ({ request }) => {
        lastUrl = request.url;
        return HttpResponse.json({ items: [], total: 0 });
      }),
      http.get(`${API}/api/v1/citizens`, () =>
        HttpResponse.json({ items: FAKE_CITIZENS, total: 3 }),
      ),
    );

    const { container } = renderRoute();
    await waitFor(() => expect(lastUrl).toContain("/api/v1/sessions"));
    expect(new URL(lastUrl).searchParams.get("status")).toBeNull();

    fireEvent.change(screen.getByLabelText("Status"), {
      target: { value: "completed" },
    });

    await waitFor(() => {
      expect(new URL(lastUrl).searchParams.get("status")).toBe("completed");
    });
    // The URL inside MemoryRouter is exposed via the document
    // location — but we use the fact that setSearchParams flushed
    // the query string. Easier: assert on the component-controlled
    // <select> which mirrors the URL through useSearchParams.
    const select = container.querySelector<HTMLSelectElement>("#filter-status");
    expect(select?.value).toBe("completed");
  });

  // Verifies the date-range filters convert to ISO datetime bounds
  // and forward as started_after / started_before.
  it("forwards from/to dates as started_after / started_before", async () => {
    let lastUrl = "";
    server.use(
      http.get(`${API}/api/v1/sessions`, ({ request }) => {
        lastUrl = request.url;
        return HttpResponse.json({ items: [], total: 0 });
      }),
      http.get(`${API}/api/v1/citizens`, () =>
        HttpResponse.json({ items: FAKE_CITIZENS, total: 3 }),
      ),
    );

    renderRoute();
    await waitFor(() => expect(lastUrl).toContain("/api/v1/sessions"));

    fireEvent.change(screen.getByLabelText("From"), {
      target: { value: "2026-04-01" },
    });
    await waitFor(() => {
      expect(new URL(lastUrl).searchParams.get("started_after")).toBe(
        "2026-04-01T00:00:00",
      );
    });

    fireEvent.change(screen.getByLabelText("To"), {
      target: { value: "2026-05-07" },
    });
    await waitFor(() => {
      expect(new URL(lastUrl).searchParams.get("started_before")).toBe(
        "2026-05-07T23:59:59.999",
      );
    });
  });

  // Verifies the citizen autocomplete: typing a search term shows
  // matching citizens, clicking one applies a citizen_id filter
  // that lands in both the URL search params and the API request.
  // Mortality: would fail if the debounce logic regressed, or if
  // selecting a citizen didn't mirror to URL state.
  it("filters by citizen via the autocomplete", async () => {
    let lastUrl = "";
    server.use(
      http.get(`${API}/api/v1/sessions`, ({ request }) => {
        lastUrl = request.url;
        return HttpResponse.json({ items: [], total: 0 });
      }),
      http.get(`${API}/api/v1/citizens`, () =>
        HttpResponse.json({ items: FAKE_CITIZENS, total: 3 }),
      ),
    );

    renderRoute();
    await waitFor(() => expect(lastUrl).toContain("/api/v1/sessions"));

    const input = screen.getByLabelText("Citizen") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "maria" } });

    // Wait through debounce + render; suggestions list mounts as a
    // <ul role="listbox">.
    const option = await waitFor(
      () => screen.getByRole("option", { name: /Maria Dela Cruz/ }),
      { timeout: 1500 },
    );
    fireEvent.click(option);

    await waitFor(() => {
      expect(new URL(lastUrl).searchParams.get("citizen_id")).toBe("c-3");
    });

    // Selected pill replaces the input.
    expect(screen.getByText("Maria Dela Cruz")).toBeInTheDocument();
  });

  // Verifies "Clear filters" wipes the URL search params back to a
  // bare /sessions and re-defaults the date range to last-7-days.
  // Mortality: would fail if clearFilters didn't reset
  // useSearchParams or if status/citizen filters lingered.
  it("clears all filters when the Clear button is clicked", async () => {
    let lastUrl = "";
    server.use(
      http.get(`${API}/api/v1/sessions`, ({ request }) => {
        lastUrl = request.url;
        return HttpResponse.json({ items: [], total: 0 });
      }),
      http.get(`${API}/api/v1/citizens`, () =>
        HttpResponse.json({ items: FAKE_CITIZENS, total: 3 }),
      ),
    );

    renderRoute("/sessions?status=completed&citizen=c-1");
    await waitFor(() => {
      const url = new URL(lastUrl);
      expect(url.searchParams.get("status")).toBe("completed");
      expect(url.searchParams.get("citizen_id")).toBe("c-1");
    });

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));

    await waitFor(() => {
      const url = new URL(lastUrl);
      expect(url.searchParams.get("status")).toBeNull();
      expect(url.searchParams.get("citizen_id")).toBeNull();
    });
    // Date filters reset to default last-7-days, so started_after
    // and started_before are still present (they're the default
    // range, not user-applied filters).
    const url = new URL(lastUrl);
    expect(url.searchParams.get("started_after")).toMatch(
      /^\d{4}-\d{2}-\d{2}T00:00:00$/,
    );
  });
});
