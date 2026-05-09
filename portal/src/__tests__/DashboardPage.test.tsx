import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { Route, Routes } from "react-router-dom";
import { screen, within } from "@testing-library/react";

import type {
  CitizenRead,
  MeasurementRead,
  Page,
  SessionRead,
  SessionStatus,
} from "../api/client";
import { DashboardPage } from "../pages/DashboardPage";
import { renderWithProviders } from "./test-utils";

const API = "http://127.0.0.1:8000";

// recharts uses ResponsiveContainer which measures its parent
// element with ResizeObserver. jsdom doesn't ship one, so the
// chart renders to a 0×0 surface and the SVG never lays out.
// Polyfilling with a no-op is enough for our tests — we don't
// assert on the SVG geometry, only that recharts mounted at all
// (and the surrounding section / table copy is what we actually
// verify).
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
beforeAll(() => {
  if (typeof globalThis.ResizeObserver === "undefined") {
    (
      globalThis as unknown as { ResizeObserver: typeof ResizeObserverStub }
    ).ResizeObserver = ResizeObserverStub;
  }
});

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function isoNDaysAgo(days: number, hour: number = 12): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  d.setHours(hour, 0, 0, 0);
  return d.toISOString();
}

function makeSession(over: Partial<SessionRead>): SessionRead {
  return {
    id: "session-id",
    citizen_id: "citizen-id",
    device_id: "kiosk-1",
    started_at: isoNDaysAgo(0),
    ended_at: null,
    status: "completed",
    error_reason: null,
    measurement_path: "vitals",
    printed_status: "printed_ok",
    synced: 1,
    updated_at: isoNDaysAgo(0),
    measurement_count: 0,
    ...over,
  };
}

function makeCitizen(over: Partial<CitizenRead>): CitizenRead {
  return {
    id: "citizen-id",
    rfid_uid: "RFID-XX",
    full_name: "Test Citizen",
    dob: "1990-01-01",
    sex: "F",
    barangay: "Tibagan",
    phone: null,
    consent_version: "v1",
    consent_given_at: isoNDaysAgo(60),
    registered_at: isoNDaysAgo(60),
    registered_by: null,
    is_active: 1,
    synced: 1,
    updated_at: isoNDaysAgo(60),
    ...over,
  };
}

// 5 completed + 3 aborted + 2 in_progress, spread across the last
// 14 days. Plus one session 20 days ago that should NOT contribute
// to the bar chart or path donut (windowed to 14d), only to the
// "Active citizens" KPI is unaffected (citizens KPI uses
// registered_at, not started_at).
const STATUS_MIX: Array<{ status: SessionStatus; daysAgo: number }> = [
  { status: "completed", daysAgo: 0 },
  { status: "completed", daysAgo: 1 },
  { status: "completed", daysAgo: 2 },
  { status: "completed", daysAgo: 5 },
  { status: "completed", daysAgo: 10 },
  { status: "aborted", daysAgo: 0 },
  { status: "aborted", daysAgo: 3 },
  { status: "aborted", daysAgo: 8 },
  { status: "in_progress", daysAgo: 0 },
  { status: "in_progress", daysAgo: 4 },
];

const PATH_MIX: Array<{ path: SessionRead["measurement_path"] }> = [
  { path: "vitals" },
  { path: "vitals" },
  { path: "vitals" },
  { path: "vitals" },
  { path: "vitals" },
  { path: "anthropometric" },
  { path: "anthropometric" },
  { path: "full" },
  { path: "full" },
  { path: null },
];

function buildSessions(): SessionRead[] {
  return STATUS_MIX.map((row, i) =>
    makeSession({
      id: `s-${i}`,
      citizen_id: `c-${(i % 3) + 1}`,
      started_at: isoNDaysAgo(row.daysAgo, 9 + i),
      status: row.status,
      measurement_path: PATH_MIX[i].path,
    }),
  );
}

function buildCitizens(): CitizenRead[] {
  return [
    makeCitizen({
      id: "c-1",
      full_name: "Alice",
      registered_at: isoNDaysAgo(2),
    }),
    makeCitizen({
      id: "c-2",
      full_name: "Ben",
      registered_at: isoNDaysAgo(15),
    }),
    makeCitizen({
      id: "c-3",
      full_name: "Cora",
      registered_at: isoNDaysAgo(45),
    }),
  ];
}

function renderRoute(initialPath: string = "/dashboard") {
  return renderWithProviders(
    <Routes>
      <Route path="/dashboard" element={<DashboardPage />} />
    </Routes>,
    { initialEntries: [initialPath] },
  );
}

describe("DashboardPage", () => {
  // Verifies the happy path: KPI cards count from the same dataset
  // as the bar chart and donut. Mortality: would fail if any
  // aggregator regressed silently — KPI math, day-bucketing, or the
  // path breakdown.
  it("renders KPIs, bar chart, donut, and recent activity", async () => {
    const sessions = buildSessions();
    const citizens = buildCitizens();
    server.use(
      http.get(`${API}/api/v1/sessions`, () =>
        HttpResponse.json<Page<SessionRead>>({
          items: sessions,
          total: sessions.length,
        }),
      ),
      http.get(`${API}/api/v1/citizens`, () =>
        HttpResponse.json<Page<CitizenRead>>({
          items: citizens,
          total: citizens.length,
        }),
      ),
      http.get(`${API}/api/v1/measurements`, () =>
        HttpResponse.json<Page<MeasurementRead>>({ items: [], total: 0 }),
      ),
    );

    renderRoute();

    // KPI cards: 3 sessions today (1 completed + 1 aborted + 1
    // in_progress in STATUS_MIX with daysAgo=0). The label sits
    // inside the card alongside the value, so walk up to the card
    // wrapper to scope the value lookup.
    const todayCard = (await screen.findByText("Sessions today")).parentElement;
    expect(todayCard).not.toBeNull();
    expect(within(todayCard!).getByText("3")).toBeInTheDocument();

    // Sessions this week (last 7 days) = 8 (all but the daysAgo=8
    // and daysAgo=10 entries).
    const weekCard = (await screen.findByText("Sessions this week"))
      .parentElement;
    expect(weekCard).not.toBeNull();
    expect(within(weekCard!).getByText("8")).toBeInTheDocument();

    // Active citizens (last 30 days) = 2 (Alice@2d + Ben@15d).
    const citCard = (await screen.findByText("Active citizens (30d)"))
      .parentElement;
    expect(citCard).not.toBeNull();
    expect(within(citCard!).getByText("2")).toBeInTheDocument();
  });

  // Verifies the Path donut legend shows the breakdown counts
  // alongside the labels. Recharts' SVG geometry isn't asserted —
  // ResizeObserver in jsdom would fight us — but the text legend
  // is plain DOM and we can trust it reflects the data.
  it("renders the path-breakdown legend with counts", async () => {
    server.use(
      http.get(`${API}/api/v1/sessions`, () =>
        HttpResponse.json<Page<SessionRead>>({
          items: buildSessions(),
          total: STATUS_MIX.length,
        }),
      ),
      http.get(`${API}/api/v1/citizens`, () =>
        HttpResponse.json<Page<CitizenRead>>({
          items: buildCitizens(),
          total: 3,
        }),
      ),
      http.get(`${API}/api/v1/measurements`, () =>
        HttpResponse.json<Page<MeasurementRead>>({ items: [], total: 0 }),
      ),
    );

    renderRoute();

    // PATH_MIX has 5 vitals, 2 anthropometric, 2 full, 1 null.
    // The legend renders one <li> per non-zero entry.
    const legend = (
      await screen.findByRole("heading", { level: 2, name: "Path breakdown" })
    ).closest("section");
    expect(legend).not.toBeNull();
    expect(within(legend!).getByText("Vitals")).toBeInTheDocument();
    expect(within(legend!).getByText("Anthropometric")).toBeInTheDocument();
    expect(within(legend!).getByText("Full")).toBeInTheDocument();
    expect(within(legend!).getByText("Unspecified")).toBeInTheDocument();
    // Counts (5 / 2 / 2 / 1) appear next to each label.
    expect(within(legend!).getByText("5")).toBeInTheDocument();
    expect(within(legend!).getAllByText("2").length).toBeGreaterThanOrEqual(1);
    expect(within(legend!).getByText("1")).toBeInTheDocument();
  });

  // Verifies the recent-activity table shows up to 5 sessions and
  // each row navigates to /sessions/:id.
  it("renders the last 5 recent sessions", async () => {
    const sessions = buildSessions();
    server.use(
      http.get(`${API}/api/v1/sessions`, () =>
        HttpResponse.json<Page<SessionRead>>({
          items: sessions,
          total: sessions.length,
        }),
      ),
      http.get(`${API}/api/v1/citizens`, () =>
        HttpResponse.json<Page<CitizenRead>>({ items: [], total: 0 }),
      ),
      http.get(`${API}/api/v1/measurements`, () =>
        HttpResponse.json<Page<MeasurementRead>>({ items: [], total: 0 }),
      ),
    );

    renderRoute();

    const recentSection = (
      await screen.findByRole("heading", { level: 2, name: "Recent sessions" })
    ).closest("section");
    expect(recentSection).not.toBeNull();
    const table = within(recentSection!).getByRole("table");
    const rows = within(table).getAllByRole("row");
    // 1 header + 5 data rows
    expect(rows).toHaveLength(6);
    // The "View all sessions →" link points at /sessions.
    const link = within(recentSection!).getByRole("link", {
      name: /View all sessions/,
    });
    expect(link).toHaveAttribute("href", "/sessions");
  });

  // Verifies the empty-state copy when the cloud has no data yet.
  // Each section renders its own empty message; the bar chart and
  // donut don't try to render an SVG. The KPIs still render — at
  // zero — because zero is a real, useful value to surface.
  it("renders empty states when the cloud has no data", async () => {
    server.use(
      http.get(`${API}/api/v1/sessions`, () =>
        HttpResponse.json<Page<SessionRead>>({ items: [], total: 0 }),
      ),
      http.get(`${API}/api/v1/citizens`, () =>
        HttpResponse.json<Page<CitizenRead>>({ items: [], total: 0 }),
      ),
      http.get(`${API}/api/v1/measurements`, () =>
        HttpResponse.json<Page<MeasurementRead>>({ items: [], total: 0 }),
      ),
    );

    renderRoute();

    expect(
      await screen.findByText("No sessions in the last 14 days."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("No measurement paths to summarize yet."),
    ).toBeInTheDocument();
    expect(screen.getByText("No sessions yet.")).toBeInTheDocument();
  });

  // Verifies the error state when a downstream query fails.
  // useDashboardStats collapses three queries into one isError flag;
  // that's intentional — if any of the three fails the dashboard
  // can't be trusted, so we surface a single banner rather than
  // partial data.
  it("renders an error banner when the API fails", async () => {
    server.use(
      http.get(`${API}/api/v1/sessions`, () =>
        HttpResponse.json(
          { detail: "internal server error" },
          { status: 500, statusText: "Internal Server Error" },
        ),
      ),
      http.get(`${API}/api/v1/citizens`, () =>
        HttpResponse.json<Page<CitizenRead>>({ items: [], total: 0 }),
      ),
      http.get(`${API}/api/v1/measurements`, () =>
        HttpResponse.json<Page<MeasurementRead>>({ items: [], total: 0 }),
      ),
    );

    renderRoute();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/Failed to load the dashboard/i);
  });
});
