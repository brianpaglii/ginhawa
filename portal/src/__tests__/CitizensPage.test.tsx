import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { Route, Routes } from "react-router-dom";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import type { CitizenRead, Page, SessionRead } from "../api/client";
import { CitizenDetailPage } from "../pages/CitizenDetailPage";
import { CitizensPage } from "../pages/CitizensPage";
import { renderWithProviders } from "./test-utils";

const API = "http://127.0.0.1:8000";

function makeCitizen(over: Partial<CitizenRead>): CitizenRead {
  return {
    id: "c-id",
    rfid_uid: "RFID-XX",
    full_name: "Citizen Name",
    dob: "1955-03-14",
    sex: "F",
    barangay: "Tibagan",
    phone: null,
    consent_version: "v1",
    consent_given_at: "2026-01-01T00:00:00+00:00",
    registered_at: "2026-01-01T00:00:00+00:00",
    registered_by: null,
    is_active: 1,
    synced: 1,
    updated_at: "2026-01-01T00:00:00+00:00",
    ...over,
  };
}

const FIVE_CITIZENS: CitizenRead[] = [
  makeCitizen({
    id: "c-1",
    full_name: "Maria Dela Cruz",
    dob: "1955-03-14",
    sex: "F",
    registered_at: "2026-05-05T10:00:00+00:00",
  }),
  makeCitizen({
    id: "c-2",
    full_name: "Juan Santos",
    dob: "1980-07-22",
    sex: "M",
    registered_at: "2026-05-04T10:00:00+00:00",
  }),
  makeCitizen({
    id: "c-3",
    full_name: "Maria Reyes",
    dob: "1972-11-02",
    sex: "F",
    registered_at: "2026-05-03T10:00:00+00:00",
  }),
  makeCitizen({
    id: "c-4",
    full_name: "Pedro Gomez",
    dob: "1990-01-15",
    sex: "M",
    registered_at: "2026-05-02T10:00:00+00:00",
  }),
  makeCitizen({
    id: "c-5",
    full_name: "Ana Valdez",
    dob: "2002-06-30",
    sex: "F",
    registered_at: "2026-05-01T10:00:00+00:00",
  }),
];

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderCitizensRoutes(initialPath: string = "/citizens") {
  return renderWithProviders(
    <Routes>
      <Route path="/citizens" element={<CitizensPage />} />
      <Route path="/citizens/:id" element={<CitizenDetailPage />} />
    </Routes>,
    { initialEntries: [initialPath] },
  );
}

describe("CitizensPage", () => {
  // Verifies the happy path: five mocked citizens render with their
  // ages computed from the dob string. Mortality: would fail if the
  // table dropped any row, or if computeAge regressed.
  it("renders all five citizens with computed ages", async () => {
    server.use(
      http.get(`${API}/api/v1/citizens`, () =>
        HttpResponse.json<Page<CitizenRead>>({
          items: FIVE_CITIZENS,
          total: FIVE_CITIZENS.length,
        }),
      ),
    );

    renderCitizensRoutes();

    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row");
    // 1 header + 5 data rows
    expect(rows).toHaveLength(6);

    expect(within(table).getByText("Maria Dela Cruz")).toBeInTheDocument();
    expect(within(table).getByText("Juan Santos")).toBeInTheDocument();
    expect(within(table).getByText("Pedro Gomez")).toBeInTheDocument();
    // Every data row carries an "Age" cell ending in "years"; assert
    // there are exactly 5 of them so we know computeAge produced a
    // value for each citizen.
    expect(within(table).getAllByText(/^\d+ years$/)).toHaveLength(5);
    expect(screen.getByText("Showing 1–5 of 5")).toBeInTheDocument();
  });

  // Verifies the client-side search filter narrows to rows whose
  // full_name contains the term (case-insensitive). After typing
  // "maria", only Dela Cruz and Reyes remain.
  it("filters rows by full_name as the user types", async () => {
    server.use(
      http.get(`${API}/api/v1/citizens`, () =>
        HttpResponse.json<Page<CitizenRead>>({
          items: FIVE_CITIZENS,
          total: FIVE_CITIZENS.length,
        }),
      ),
    );

    renderCitizensRoutes();

    const search = await screen.findByPlaceholderText("Search by name…");
    fireEvent.change(search, { target: { value: "maria" } });

    // Search is debounced 250 ms; wait for the filter to kick in.
    await waitFor(() => {
      expect(screen.getByText("Maria Dela Cruz")).toBeInTheDocument();
      expect(screen.getByText("Maria Reyes")).toBeInTheDocument();
      expect(screen.queryByText("Juan Santos")).not.toBeInTheDocument();
      expect(screen.queryByText("Pedro Gomez")).not.toBeInTheDocument();
    });
  });

  // Verifies clicking a row navigates to /citizens/:id. The detail
  // page renders the citizen's name, confirming the param flowed
  // through the router.
  it("navigates to the citizen detail page when a row is clicked", async () => {
    server.use(
      http.get(`${API}/api/v1/citizens`, () =>
        HttpResponse.json<Page<CitizenRead>>({
          items: FIVE_CITIZENS,
          total: FIVE_CITIZENS.length,
        }),
      ),
      http.get(`${API}/api/v1/citizens/c-1`, () =>
        HttpResponse.json(FIVE_CITIZENS[0]),
      ),
      http.get(`${API}/api/v1/sessions`, () =>
        HttpResponse.json<Page<SessionRead>>({ items: [], total: 0 }),
      ),
    );

    renderCitizensRoutes();

    const row = await screen.findByRole("row", {
      name: /Open citizen Maria Dela Cruz/,
    });
    fireEvent.click(row);

    expect(
      await screen.findByRole("heading", { level: 1, name: "Maria Dela Cruz" }),
    ).toBeInTheDocument();
  });

  // Verifies the 404 path on the citizen detail. When the API
  // returns 404 for a citizen lookup, the page shows the dedicated
  // not-found view and does NOT fire the dependent sessions query.
  // Mortality: would fail if the page rendered partial data, or if
  // the sessions query fired (MSW's onUnhandledRequest:"error"
  // would also catch it).
  it("renders not-found for an unknown citizen id", async () => {
    server.use(
      http.get(`${API}/api/v1/citizens/missing`, () =>
        HttpResponse.json(
          { detail: "citizen missing not found" },
          { status: 404 },
        ),
      ),
    );

    renderCitizensRoutes("/citizens/missing");

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Citizen not found",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "← Back to citizens" }),
    ).toBeInTheDocument();
  });
});
