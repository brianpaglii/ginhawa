import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { screen, waitFor, within } from "@testing-library/react";

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

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("SessionsPage", () => {
  // Verifies the happy path: three sessions render in the order the
  // server returned them (newest-first ordering is the cloud's
  // responsibility — the server contract is verified separately in
  // the cloud test suite). Citizen names look up correctly via the
  // citizens map built from the second query.
  it("renders mocked sessions with citizen names, newest first", async () => {
    const citizens: Page<CitizenRead> = {
      total: 2,
      items: [
        makeCitizen({ id: "c-1", full_name: "Alice Reyes" }),
        makeCitizen({ id: "c-2", full_name: "Ben Cruz" }),
      ],
    };
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
      http.get(`${API}/api/v1/citizens`, () => HttpResponse.json(citizens)),
    );

    renderWithProviders(<SessionsPage />);

    // Wait for the sessions table to land.
    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row");
    // 1 header + 3 data rows
    expect(rows).toHaveLength(4);

    // Order check — first data row is s-1 (Alice, in-progress),
    // last is s-3 (Alice again, aborted).
    expect(within(rows[1]).getByText("Alice Reyes")).toBeInTheDocument();
    expect(within(rows[1]).getByText("In progress")).toBeInTheDocument();
    expect(within(rows[1]).getByText("full")).toBeInTheDocument();
    expect(within(rows[1]).getByText("5")).toBeInTheDocument();

    expect(within(rows[2]).getByText("Ben Cruz")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Completed")).toBeInTheDocument();

    expect(within(rows[3]).getByText("Alice Reyes")).toBeInTheDocument();
    expect(within(rows[3]).getByText("Aborted")).toBeInTheDocument();
    expect(within(rows[3]).getByText("anthropometric")).toBeInTheDocument();
    expect(within(rows[3]).getByText("1")).toBeInTheDocument();

    // Pagination footer reflects "all on one page".
    expect(screen.getByText("Showing 1–3 of 3")).toBeInTheDocument();
  });

  // Verifies the empty state when total = 0 — no table, an explicit
  // "no sessions yet" message instead. Pagination is hidden because
  // there are no pages to navigate.
  it("renders the empty state when no sessions exist", async () => {
    server.use(
      http.get(`${API}/api/v1/sessions`, () =>
        HttpResponse.json({ items: [], total: 0 }),
      ),
      http.get(`${API}/api/v1/citizens`, () =>
        HttpResponse.json({ items: [], total: 0 }),
      ),
    );

    renderWithProviders(<SessionsPage />);

    expect(await screen.findByText("No sessions yet.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Next" }),
    ).not.toBeInTheDocument();
  });

  // Verifies the error state when the API returns 500. The page
  // renders an alert with a recognizable message instead of a partially
  // populated table.
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

    renderWithProviders(<SessionsPage />);

    const alert = await waitFor(() => screen.getByRole("alert"));
    expect(alert).toHaveTextContent(/internal server error|request failed/i);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
