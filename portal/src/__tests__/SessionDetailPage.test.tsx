import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { Route, Routes } from "react-router-dom";
import { screen, within } from "@testing-library/react";

import { SessionDetailPage } from "../pages/SessionDetailPage";
import type {
  AuditLogRead,
  CitizenRead,
  MeasurementRead,
  Page,
  SessionRead,
  UserRead,
} from "../api/client";
import { FAKE_BHW_USER, makeAuth, renderWithProviders } from "./test-utils";

const API = "http://127.0.0.1:8000";
const SESSION_ID = "session-1";

const FAKE_ADMIN_USER: UserRead = {
  id: "admin-1",
  username: "admin",
  full_name: "Admin User",
  role: "admin",
  assigned_barangay: null,
  is_active: 1,
  created_at: "2026-01-01T00:00:00+00:00",
  last_login_at: null,
};

const SESSION: SessionRead = {
  id: SESSION_ID,
  citizen_id: "citizen-1",
  device_id: "kiosk-1",
  started_at: "2026-05-06T10:00:00+00:00",
  ended_at: "2026-05-06T10:08:30+00:00",
  status: "completed",
  error_reason: null,
  measurement_path: "full",
  printed_status: "printed_ok",
  synced: 1,
  updated_at: "2026-05-06T10:08:30+00:00",
  measurement_count: 3,
};

const CITIZEN: CitizenRead = {
  id: "citizen-1",
  rfid_uid: "RFID-123",
  full_name: "Maria Tibagan",
  dob: "1990-01-01",
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
};

function makeMeasurement(overrides: Partial<MeasurementRead>): MeasurementRead {
  return {
    id: "m-id",
    session_id: SESSION_ID,
    type: "systolic_bp",
    value: 120,
    unit: "mmHg",
    source_device: "omron_hem7155t",
    measured_at: "2026-05-06T10:01:00+00:00",
    is_valid: 1,
    validation_notes: null,
    raw_json: null,
    synced: 1,
    updated_at: "2026-05-06T10:01:00+00:00",
    ...overrides,
  };
}

function emptyPage<T>(): Page<T> {
  return { items: [], total: 0 };
}

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderDetail(options: { user?: UserRead; sessionId?: string } = {}) {
  const user = options.user ?? FAKE_BHW_USER;
  const id = options.sessionId ?? SESSION_ID;
  return renderWithProviders(
    <Routes>
      <Route path="/sessions/:id" element={<SessionDetailPage />} />
    </Routes>,
    {
      auth: makeAuth({ user, status: "authenticated" }),
      initialEntries: [`/sessions/${id}`],
    },
  );
}

describe("SessionDetailPage", () => {
  // Verifies the happy path: session header (citizen + RFID + status
  // + duration), measurements table, and (for an admin) the audit
  // timeline all render with mocked data. Mortality: would fail if
  // any section silently dropped its query result or if the parallel
  // fetches got serialised behind one another.
  it("renders header, measurements, and audit log for an admin user", async () => {
    const measurements: Page<MeasurementRead> = {
      total: 3,
      items: [
        makeMeasurement({
          id: "m-1",
          type: "systolic_bp",
          value: 128,
          unit: "mmHg",
          measured_at: "2026-05-06T10:01:00+00:00",
        }),
        makeMeasurement({
          id: "m-2",
          type: "diastolic_bp",
          value: 82,
          unit: "mmHg",
          measured_at: "2026-05-06T10:01:00+00:00",
        }),
        makeMeasurement({
          id: "m-3",
          type: "weight",
          value: 65.4,
          unit: "kg",
          source_device: "xiaomi_s200_ble",
          measured_at: "2026-05-06T10:05:00+00:00",
        }),
      ],
    };
    const audit: Page<AuditLogRead> = {
      total: 2,
      items: [
        // Server returns DESC; the page reverses to ASC for the
        // timeline view.
        {
          id: 2,
          timestamp: "2026-05-06T10:08:30+00:00",
          actor_type: "kiosk",
          actor_id: null,
          action: "update",
          object_type: "session",
          object_id: SESSION_ID,
          ip_address: null,
          details: JSON.stringify({
            changed: ["status", "ended_at"],
            status_from: "in_progress",
            status_to: "completed",
          }),
          synced: 1,
        },
        {
          id: 1,
          timestamp: "2026-05-06T10:00:00+00:00",
          actor_type: "citizen",
          actor_id: CITIZEN.id,
          action: "create",
          object_type: "session",
          object_id: SESSION_ID,
          ip_address: null,
          details: JSON.stringify({
            device_id: "kiosk-1",
            measurement_path: "full",
          }),
          synced: 1,
        },
      ],
    };

    server.use(
      http.get(`${API}/api/v1/sessions/${SESSION_ID}`, () =>
        HttpResponse.json(SESSION),
      ),
      http.get(`${API}/api/v1/citizens/${CITIZEN.id}`, () =>
        HttpResponse.json(CITIZEN),
      ),
      http.get(`${API}/api/v1/measurements`, ({ request }) => {
        const isValid = new URL(request.url).searchParams.get("is_valid");
        if (isValid === "false") {
          return HttpResponse.json(emptyPage<MeasurementRead>());
        }
        return HttpResponse.json(measurements);
      }),
      http.get(`${API}/api/v1/audit-log`, () => HttpResponse.json(audit)),
    );

    renderDetail({ user: FAKE_ADMIN_USER });

    // Header: citizen name, RFID, status, duration.
    expect(await screen.findByText("Maria Tibagan")).toBeInTheDocument();
    expect(screen.getByText("RFID-123")).toBeInTheDocument();
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText("8m 30s")).toBeInTheDocument();

    // Measurements table contains all three rows.
    const measurementsSection = screen
      .getByRole("heading", { level: 2, name: "Measurements" })
      .closest("section");
    expect(measurementsSection).not.toBeNull();
    const mTable = within(measurementsSection!).getByRole("table");
    expect(within(mTable).getByText("Systolic BP")).toBeInTheDocument();
    expect(within(mTable).getByText("Diastolic BP")).toBeInTheDocument();
    expect(within(mTable).getByText("Weight")).toBeInTheDocument();
    expect(within(mTable).getByText("128 mmHg")).toBeInTheDocument();
    expect(within(mTable).getByText("65.4 kg")).toBeInTheDocument();

    // Audit timeline: 2 entries, ordered oldest-first (create then
    // update). The page reverses the server's DESC order.
    const auditSection = screen
      .getByRole("heading", { level: 2, name: "Audit log" })
      .closest("section");
    expect(auditSection).not.toBeNull();
    const auditItems = within(auditSection!).getAllByRole("listitem");
    expect(auditItems).toHaveLength(2);
    expect(within(auditItems[0]).getByText("create")).toBeInTheDocument();
    expect(within(auditItems[1]).getByText("update")).toBeInTheDocument();
  });

  // Verifies the BHW path hides the audit log section: BHWs lack
  // audit_log:read scope on the cloud, and firing the query just to
  // catch a 403 is pointless. The header + measurements sections
  // still render normally.
  it("hides the audit-log section for non-admin users", async () => {
    server.use(
      http.get(`${API}/api/v1/sessions/${SESSION_ID}`, () =>
        HttpResponse.json(SESSION),
      ),
      http.get(`${API}/api/v1/citizens/${CITIZEN.id}`, () =>
        HttpResponse.json(CITIZEN),
      ),
      http.get(`${API}/api/v1/measurements`, () =>
        HttpResponse.json(emptyPage<MeasurementRead>()),
      ),
      // Note: no /audit-log handler installed — if the page tried to
      // fetch it, MSW would error out (onUnhandledRequest: "error").
    );

    renderDetail({ user: FAKE_BHW_USER });

    expect(await screen.findByText("Maria Tibagan")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { level: 2, name: "Audit log" }),
    ).not.toBeInTheDocument();
  });

  // Verifies the empty-measurements state — no table, an explicit
  // "No measurements captured." message instead.
  it("renders empty-measurements state for sessions with no measurements", async () => {
    server.use(
      http.get(`${API}/api/v1/sessions/${SESSION_ID}`, () =>
        HttpResponse.json({ ...SESSION, measurement_count: 0 }),
      ),
      http.get(`${API}/api/v1/citizens/${CITIZEN.id}`, () =>
        HttpResponse.json(CITIZEN),
      ),
      http.get(`${API}/api/v1/measurements`, () =>
        HttpResponse.json(emptyPage<MeasurementRead>()),
      ),
    );

    renderDetail({ user: FAKE_BHW_USER });

    expect(
      await screen.findByText("No measurements captured."),
    ).toBeInTheDocument();
    // The measurements section's heading is still there, just no
    // table inside it.
    const measurementsSection = screen
      .getByRole("heading", { level: 2, name: "Measurements" })
      .closest("section");
    expect(within(measurementsSection!).queryByRole("table")).toBeNull();
  });

  // Verifies the 404 path: when the session API returns 404, the
  // page shows a "not found" message with a link back to the list
  // and does not fire dependent queries (citizen, measurements).
  // Mortality: would fail if the page rendered partial data with an
  // error banner instead of the dedicated not-found view.
  it("renders not-found view when the session does not exist", async () => {
    server.use(
      http.get(`${API}/api/v1/sessions/${SESSION_ID}`, () =>
        HttpResponse.json(
          { detail: `session ${SESSION_ID} not found` },
          { status: 404 },
        ),
      ),
      // No citizen / measurements / audit-log handlers — if the page
      // tried to call them the unhandled-request guard would fail.
    );

    renderDetail({ user: FAKE_BHW_USER });

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Session not found",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "← Back to sessions" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { level: 2, name: "Measurements" }),
    ).not.toBeInTheDocument();
  });
});
