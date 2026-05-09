import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { Route, Routes } from "react-router-dom";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SessionDetailPage } from "../pages/SessionDetailPage";
import type {
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
  measurement_count: 2,
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

const VALID_M: MeasurementRead = makeMeasurement({
  id: "m-valid",
  type: "systolic_bp",
  value: 128,
  unit: "mmHg",
});
const INVALID_M: MeasurementRead = makeMeasurement({
  id: "m-invalid",
  type: "diastolic_bp",
  value: 82,
  unit: "mmHg",
  is_valid: 0,
  validation_notes: "invalidated: cuff slipped",
});

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderDetail(user: UserRead) {
  return renderWithProviders(
    <Routes>
      <Route path="/sessions/:id" element={<SessionDetailPage />} />
    </Routes>,
    {
      auth: makeAuth({ user, status: "authenticated" }),
      initialEntries: [`/sessions/${SESSION_ID}`],
    },
  );
}

function emptyPage<T>(): Page<T> {
  return { items: [], total: 0 };
}

// Default handlers for the queries the page fires before the user
// touches anything: session, citizen, measurements (split by is_valid),
// and the audit log (admin only — handled identically here).
function installBaseHandlers() {
  server.use(
    http.get(`${API}/api/v1/sessions/${SESSION_ID}`, () =>
      HttpResponse.json(SESSION),
    ),
    http.get(`${API}/api/v1/citizens/${CITIZEN.id}`, () =>
      HttpResponse.json(CITIZEN),
    ),
    http.get(`${API}/api/v1/measurements`, ({ request }) => {
      const url = new URL(request.url);
      const isValid = url.searchParams.get("is_valid");
      if (isValid === "true") {
        return HttpResponse.json<Page<MeasurementRead>>({
          items: [VALID_M],
          total: 1,
        });
      }
      return HttpResponse.json<Page<MeasurementRead>>({
        items: [INVALID_M],
        total: 1,
      });
    }),
    http.get(`${API}/api/v1/audit-log`, () =>
      HttpResponse.json(emptyPage<unknown>()),
    ),
  );
}

describe("InvalidateMeasurement", () => {
  // Verifies admin sees the Invalidate button only on the valid row.
  // Mortality: would fail if the action column leaked onto an
  // already-invalid row (which would let an admin double-invalidate
  // and write nonsense to validation_notes).
  it("admin sees Invalidate button on valid measurements only", async () => {
    installBaseHandlers();
    renderDetail(FAKE_ADMIN_USER);

    const validRow = (await screen.findByText("128 mmHg")).closest("tr");
    expect(validRow).not.toBeNull();
    expect(
      within(validRow!).getByRole("button", { name: "Invalidate" }),
    ).toBeInTheDocument();

    const invalidRow = screen.getByText("82 mmHg").closest("tr");
    expect(invalidRow).not.toBeNull();
    expect(
      within(invalidRow!).queryByRole("button", { name: "Invalidate" }),
    ).toBeNull();
    // Visual treatment for the invalid row: badge + strike-through.
    expect(within(invalidRow!).getByText("Invalid")).toBeInTheDocument();
  });

  // Verifies BHW does NOT see any Invalidate buttons. The cloud also
  // enforces this server-side via require_scope("measurements:write")
  // — this test pins the UI gate.
  it("BHW does not see Invalidate buttons", async () => {
    installBaseHandlers();
    renderDetail(FAKE_BHW_USER);

    await screen.findByText("128 mmHg");
    expect(screen.queryByRole("button", { name: "Invalidate" })).toBeNull();
  });

  // Verifies the modal validates the reason min-length before
  // enabling the Confirm button and that the API request lands with
  // the right body.
  it("requires a reason and submits correctly", async () => {
    installBaseHandlers();
    let receivedBody: { reason?: string } | null = null;
    let invalidatedOnce = false;
    server.use(
      http.patch(
        `${API}/api/v1/measurements/${VALID_M.id}/invalidate`,
        async ({ request }) => {
          receivedBody = (await request.json()) as { reason?: string };
          invalidatedOnce = true;
          return HttpResponse.json({
            ...VALID_M,
            is_valid: 0,
            validation_notes: `invalidated: ${receivedBody.reason}`,
          });
        },
      ),
      // Refetch after success: now the valid pane is empty and the
      // invalid pane gains the row.
      http.get(`${API}/api/v1/measurements`, ({ request }) => {
        const isValid = new URL(request.url).searchParams.get("is_valid");
        if (!invalidatedOnce) {
          return HttpResponse.json<Page<MeasurementRead>>(
            isValid === "true"
              ? { items: [VALID_M], total: 1 }
              : { items: [INVALID_M], total: 1 },
          );
        }
        return HttpResponse.json<Page<MeasurementRead>>(
          isValid === "true"
            ? emptyPage<MeasurementRead>()
            : {
                items: [
                  INVALID_M,
                  {
                    ...VALID_M,
                    is_valid: 0,
                    validation_notes:
                      "invalidated: Cuff slipped during measurement",
                  },
                ],
                total: 2,
              },
        );
      }),
    );

    const user = userEvent.setup();
    renderDetail(FAKE_ADMIN_USER);

    await user.click(await screen.findByRole("button", { name: "Invalidate" }));

    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByRole("heading", { name: "Invalidate measurement?" }),
    ).toBeInTheDocument();

    const confirmBtn = within(dialog).getByRole("button", {
      name: "Invalidate",
    });
    expect(confirmBtn).toBeDisabled();

    // Below min length (5) — still disabled.
    const textarea = within(dialog).getByRole("textbox");
    await user.type(textarea, "abc");
    expect(confirmBtn).toBeDisabled();

    // At/above min length — enabled. Submit.
    await user.clear(textarea);
    await user.type(textarea, "Cuff slipped during measurement");
    expect(confirmBtn).toBeEnabled();
    await user.click(confirmBtn);

    await waitFor(() => {
      expect(receivedBody).toEqual({
        reason: "Cuff slipped during measurement",
      });
    });
    // Success toast + dialog closed.
    expect(
      await screen.findByText("Measurement marked invalid"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  // Verifies a 403 surfaces an error toast without closing the modal,
  // so the BHW (or impersonating user) can see the rejection without
  // losing the reason text.
  it("403 keeps modal open and shows error toast", async () => {
    installBaseHandlers();
    server.use(
      http.patch(`${API}/api/v1/measurements/${VALID_M.id}/invalidate`, () =>
        HttpResponse.json({ detail: "forbidden" }, { status: 403 }),
      ),
    );

    const user = userEvent.setup();
    renderDetail(FAKE_ADMIN_USER);

    await user.click(await screen.findByRole("button", { name: "Invalidate" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(
      within(dialog).getByRole("textbox"),
      "Reason that's long enough",
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Invalidate" }),
    );

    expect(
      await screen.findByText("Could not invalidate measurement"),
    ).toBeInTheDocument();
    // Modal is still mounted.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  // Verifies Cancel closes the modal and does not fire the request.
  it("cancel closes the modal without calling the API", async () => {
    installBaseHandlers();
    let invalidateCalled = false;
    server.use(
      http.patch(`${API}/api/v1/measurements/${VALID_M.id}/invalidate`, () => {
        invalidateCalled = true;
        return HttpResponse.json(VALID_M);
      }),
    );

    const user = userEvent.setup();
    renderDetail(FAKE_ADMIN_USER);

    await user.click(await screen.findByRole("button", { name: "Invalidate" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(invalidateCalled).toBe(false);
  });
});
