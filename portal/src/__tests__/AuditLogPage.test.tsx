import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { Route, Routes } from "react-router-dom";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import type { AuditLogRead, Page, UserRead } from "../api/client";
import { AuditLogPage } from "../pages/AuditLogPage";
import { CitizenDetailPage } from "../pages/CitizenDetailPage";
import { SessionDetailPage } from "../pages/SessionDetailPage";
import { FAKE_BHW_USER, makeAuth, renderWithProviders } from "./test-utils";

const API = "http://127.0.0.1:8000";

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

function makeEntry(over: Partial<AuditLogRead>): AuditLogRead {
  return {
    id: 0,
    timestamp: "2026-05-07T09:00:00+00:00",
    actor_type: "bhw",
    actor_id: "user-1",
    action: "login",
    object_type: null,
    object_id: null,
    ip_address: null,
    details: null,
    synced: 1,
    ...over,
  };
}

const ENTRIES: AuditLogRead[] = [
  makeEntry({
    id: 1,
    timestamp: "2026-05-07T09:00:00+00:00",
    actor_type: "bhw",
    actor_id: "bhw-user-id-aaaa-bbbb-cccc-dddd",
    action: "login",
    object_type: null,
    object_id: null,
    details: JSON.stringify({ role: "bhw", scopes: ["sessions:read"] }),
  }),
  makeEntry({
    id: 2,
    timestamp: "2026-05-07T09:01:00+00:00",
    actor_type: "kiosk",
    actor_id: null,
    action: "fsm.rfid_scanned",
    object_type: "session",
    object_id: "session-abc-123-xyz-456-def",
    details: JSON.stringify({ rfid: "0x1234" }),
  }),
  makeEntry({
    id: 3,
    timestamp: "2026-05-07T09:02:00+00:00",
    actor_type: "kiosk",
    actor_id: null,
    action: "fsm.session_started",
    object_type: "session",
    object_id: "session-abc-123-xyz-456-def",
    details: JSON.stringify({ measurement_path: "full" }),
  }),
];

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderAdmin(initialPath: string = "/audit-log") {
  return renderWithProviders(
    <Routes>
      <Route path="/audit-log" element={<AuditLogPage />} />
      <Route path="/sessions/:id" element={<SessionDetailPage />} />
      <Route path="/citizens/:id" element={<CitizenDetailPage />} />
    </Routes>,
    {
      auth: makeAuth({ user: FAKE_ADMIN_USER, status: "authenticated" }),
      initialEntries: [initialPath],
    },
  );
}

describe("AuditLogPage", () => {
  // Verifies the admin happy path: rows render with timestamp,
  // actor pill+id, action string, and object cell. The kiosk
  // entries show a session link to /sessions/...
  it("renders audit entries with linked object cells", async () => {
    server.use(
      http.get(`${API}/api/v1/audit-log`, () =>
        HttpResponse.json<Page<AuditLogRead>>({
          items: ENTRIES,
          total: ENTRIES.length,
        }),
      ),
    );

    renderAdmin();

    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row");
    // 1 header + 3 data rows
    expect(rows).toHaveLength(4);

    expect(within(table).getByText("login")).toBeInTheDocument();
    expect(within(table).getByText("fsm.rfid_scanned")).toBeInTheDocument();
    expect(within(table).getByText("fsm.session_started")).toBeInTheDocument();

    // Object link points at /sessions/<id> for the session-typed rows.
    const sessionLinks = within(table).getAllByRole("link", {
      name: /^session /,
    });
    expect(sessionLinks).toHaveLength(2);
    expect(sessionLinks[0]).toHaveAttribute(
      "href",
      "/sessions/session-abc-123-xyz-456-def",
    );
  });

  // Verifies the actor_type filter sends the right query string. We
  // capture the request URL via an MSW handler and assert it on
  // change.
  it("forwards actor_type filter as a query param", async () => {
    let lastUrl = "";
    server.use(
      http.get(`${API}/api/v1/audit-log`, ({ request }) => {
        lastUrl = request.url;
        return HttpResponse.json<Page<AuditLogRead>>({
          items: [],
          total: 0,
        });
      }),
    );

    renderAdmin();

    // Wait for the initial fetch to land before changing the filter,
    // otherwise we might race the change against the first render.
    await waitFor(() => {
      expect(lastUrl).toContain("/api/v1/audit-log");
    });
    expect(new URL(lastUrl).searchParams.get("actor_type")).toBeNull();

    fireEvent.change(screen.getByLabelText("Actor type"), {
      target: { value: "kiosk" },
    });
    await waitFor(() => {
      expect(new URL(lastUrl).searchParams.get("actor_type")).toBe("kiosk");
    });
  });

  // Verifies that toggling the action_prefix input forwards
  // action_prefix as a query param (debounced ~250 ms). Assertion
  // tolerates the debounce by waiting for the eventual URL.
  it("forwards action_prefix filter (debounced)", async () => {
    let lastUrl = "";
    server.use(
      http.get(`${API}/api/v1/audit-log`, ({ request }) => {
        lastUrl = request.url;
        return HttpResponse.json<Page<AuditLogRead>>({
          items: [],
          total: 0,
        });
      }),
    );

    renderAdmin();
    await waitFor(() => expect(lastUrl).toContain("/api/v1/audit-log"));

    fireEvent.change(screen.getByLabelText("Action starts with"), {
      target: { value: "fsm." },
    });

    await waitFor(
      () => {
        expect(new URL(lastUrl).searchParams.get("action_prefix")).toBe("fsm.");
      },
      { timeout: 1500 },
    );
  });

  // Verifies the Expand button parses the JSON details and renders
  // pretty-printed contents. Mortality: would fail if the JSON
  // pretty-print regressed to a raw string.
  it("expands details JSON on demand", async () => {
    server.use(
      http.get(`${API}/api/v1/audit-log`, () =>
        HttpResponse.json<Page<AuditLogRead>>({
          items: [ENTRIES[0]],
          total: 1,
        }),
      ),
    );

    renderAdmin();

    const expandBtn = await screen.findByRole("button", { name: "Expand" });
    fireEvent.click(expandBtn);

    // After expand, the pretty-printed JSON appears in a <pre>.
    expect(
      await screen.findByText(/"role": "bhw"/, { exact: false }),
    ).toBeInTheDocument();
    expect(screen.getByText(/"scopes":/, { exact: false })).toBeInTheDocument();

    // Toggle collapses again.
    fireEvent.click(screen.getByRole("button", { name: "Collapse" }));
    expect(screen.queryByText(/"scopes":/)).not.toBeInTheDocument();
  });

  // Verifies admin-only access: a BHW user sees the 403 view and
  // no fetch is fired (MSW's onUnhandledRequest:"error" guards
  // against the page attempting to call the API).
  it("denies access for non-admin users", async () => {
    renderWithProviders(
      <Routes>
        <Route path="/audit-log" element={<AuditLogPage />} />
      </Routes>,
      {
        auth: makeAuth({ user: FAKE_BHW_USER, status: "authenticated" }),
        initialEntries: ["/audit-log"],
      },
    );

    expect(
      screen.getByRole("heading", { level: 1, name: "403 — Admin only" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "← Back to sessions" }),
    ).toBeInTheDocument();
  });

  // Verifies pagination preserves the active filters: clicking Next
  // sends offset=PAGE_SIZE while the actor_type filter is still
  // present in the query string. Mortality: would fail if the page
  // accidentally reset filters when navigating.
  it("preserves filters across pagination", async () => {
    let lastUrl = "";
    server.use(
      http.get(`${API}/api/v1/audit-log`, ({ request }) => {
        lastUrl = request.url;
        return HttpResponse.json<Page<AuditLogRead>>({
          items: ENTRIES,
          total: 200,
        });
      }),
    );

    renderAdmin();
    await waitFor(() => expect(lastUrl).toContain("/api/v1/audit-log"));

    fireEvent.change(screen.getByLabelText("Actor type"), {
      target: { value: "kiosk" },
    });
    await waitFor(() => {
      expect(new URL(lastUrl).searchParams.get("actor_type")).toBe("kiosk");
    });

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => {
      const url = new URL(lastUrl);
      expect(url.searchParams.get("actor_type")).toBe("kiosk");
      expect(url.searchParams.get("offset")).toBe("50");
    });
  });
});
