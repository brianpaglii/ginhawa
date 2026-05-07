// Typed API client for the GINHAWA cloud backend.
//
// Type definitions are hand-written from the cloud's pydantic schemas
// (cloud/src/ginhawa_cloud/api/schemas.py). When the cloud ships a
// schema change we mirror it here; we do NOT use openapi-typescript
// codegen — the surface is small and a hand-written client documents
// the contract better.

const TOKEN_STORAGE_KEY = "ginhawa.auth.token";
const DEFAULT_API_URL = "http://127.0.0.1:8000";

const apiBaseUrl = (
  import.meta.env.VITE_CLOUD_API_URL ?? DEFAULT_API_URL
).replace(/\/+$/, "");

// ---------------------------------------------------------------------------
// Enum aliases — mirror cloud schemas.py
// ---------------------------------------------------------------------------

export type Sex = "M" | "F" | "O";
export type SessionStatus = "in_progress" | "completed" | "aborted" | "error";
export type MeasurementPath = "vitals" | "anthropometric" | "full";
export type PrintedStatus =
  | "not_requested"
  | "printed_ok"
  | "paper_out_pre"
  | "paper_out_mid"
  | "print_failed";
export type MeasurementType =
  | "systolic_bp"
  | "diastolic_bp"
  | "spo2"
  | "heart_rate"
  | "temperature"
  | "height"
  | "weight"
  | "bmi";
export type ActorType = "citizen" | "bhw" | "system" | "admin" | "kiosk";
export type Role = "bhw" | "admin" | "data_viewer";

// ---------------------------------------------------------------------------
// Request / response shapes
// ---------------------------------------------------------------------------

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: "bearer";
  expires_at: string;
}

export interface UserRead {
  id: string;
  username: string;
  full_name: string;
  role: Role;
  assigned_barangay: string | null;
  is_active: number;
  created_at: string;
  last_login_at: string | null;
}

export interface CitizenRead {
  id: string;
  rfid_uid: string;
  full_name: string;
  dob: string;
  sex: Sex;
  barangay: string;
  phone: string | null;
  consent_version: string;
  consent_given_at: string;
  registered_at: string;
  registered_by: string | null;
  is_active: number;
  synced: number;
  updated_at: string;
}

export interface SessionRead {
  id: string;
  citizen_id: string;
  device_id: string;
  started_at: string;
  ended_at: string | null;
  status: SessionStatus;
  error_reason: string | null;
  measurement_path: MeasurementPath | null;
  printed_status: PrintedStatus;
  synced: number;
  updated_at: string;
  // Aggregated count rolled up server-side so the BHW portal renders
  // per-row counts without an N+1.
  measurement_count: number;
}

export interface MeasurementRead {
  id: string;
  session_id: string;
  type: MeasurementType;
  value: number;
  unit: string;
  source_device: string;
  measured_at: string;
  is_valid: number;
  validation_notes: string | null;
  raw_json: string | null;
  synced: number;
  updated_at: string;
}

export interface AuditLogRead {
  id: number;
  timestamp: string;
  actor_type: ActorType;
  actor_id: string | null;
  action: string;
  object_type: string | null;
  object_id: string | null;
  ip_address: string | null;
  details: string | null;
  synced: number;
}

// Pagination envelope (cloud schemas.py: `class Page(BaseModel, Generic[T])`).
export interface Page<T> {
  items: T[];
  total: number;
}

// ---------------------------------------------------------------------------
// Query-param shapes for list endpoints
// ---------------------------------------------------------------------------

export interface ListSessionsParams {
  citizen_id?: string;
  status?: SessionStatus;
  started_after?: string;
  started_before?: string;
  barangay?: string;
  limit?: number;
  offset?: number;
}

export interface ListCitizensParams {
  barangay?: string;
  is_active?: boolean;
  limit?: number;
  offset?: number;
}

export interface ListMeasurementsParams {
  session_id?: string;
  citizen_id?: string;
  type?: MeasurementType;
  measured_after?: string;
  measured_before?: string;
  is_valid?: boolean;
  limit?: number;
  offset?: number;
}

export interface ListAuditLogParams {
  actor_type?: ActorType;
  actor_id?: string;
  action?: string;
  object_type?: string;
  object_id?: string;
  timestamp_after?: string;
  timestamp_before?: string;
  limit?: number;
  offset?: number;
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

// FastAPI's default error envelope is `{ detail: string | { ... }[] }`.
// We pull `detail` out when present and fall back to the status text.
export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export class NetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NetworkError";
  }
}

// ---------------------------------------------------------------------------
// Token storage — localStorage only (no cookies in v1).
// ---------------------------------------------------------------------------

export function readToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeToken(token: string | null): void {
  try {
    if (token === null) {
      window.localStorage.removeItem(TOKEN_STORAGE_KEY);
    } else {
      window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
    }
  } catch {
    // Storage may be disabled (Safari private mode etc.); fail silently.
  }
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

type QueryValue = string | number | boolean | undefined | null;

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  // Accept any object whose values are query-encodable primitives. We
  // can't pin this to ``Record<string, QueryValue>`` because TS treats
  // explicit interfaces (ListSessionsParams etc.) as non-assignable to
  // index signatures.
  query?: Readonly<Record<string, QueryValue>> | object;
  // Some endpoints (login) explicitly do NOT need a token.
  authenticated?: boolean;
}

class ApiClient {
  async login(payload: LoginRequest): Promise<LoginResponse> {
    const response = await this.request<LoginResponse>("/api/v1/auth/login", {
      method: "POST",
      body: payload,
      authenticated: false,
    });
    writeToken(response.access_token);
    return response;
  }

  async logout(): Promise<void> {
    try {
      await this.request<{ status: string }>("/api/v1/auth/logout", {
        method: "POST",
      });
    } finally {
      // Clear locally regardless of server response — the token is
      // stateless on the cloud side anyway.
      writeToken(null);
    }
  }

  getMe(): Promise<UserRead> {
    return this.request<UserRead>("/api/v1/users/me");
  }

  listSessions(params: ListSessionsParams = {}): Promise<Page<SessionRead>> {
    return this.request<Page<SessionRead>>("/api/v1/sessions", {
      query: params,
    });
  }

  getSession(id: string): Promise<SessionRead> {
    return this.request<SessionRead>(
      `/api/v1/sessions/${encodeURIComponent(id)}`,
    );
  }

  listCitizens(params: ListCitizensParams = {}): Promise<Page<CitizenRead>> {
    return this.request<Page<CitizenRead>>("/api/v1/citizens", {
      query: params,
    });
  }

  listMeasurements(
    params: ListMeasurementsParams = {},
  ): Promise<Page<MeasurementRead>> {
    return this.request<Page<MeasurementRead>>("/api/v1/measurements", {
      query: params,
    });
  }

  listAuditLog(params: ListAuditLogParams = {}): Promise<Page<AuditLogRead>> {
    return this.request<Page<AuditLogRead>>("/api/v1/audit-log", {
      query: params,
    });
  }

  // -------------------------------------------------------------------------
  // Internals
  // -------------------------------------------------------------------------

  private buildUrl(path: string, query?: RequestOptions["query"]): string {
    const url = new URL(`${apiBaseUrl}${path}`);
    if (query) {
      for (const [key, value] of Object.entries(
        query as Record<string, unknown>,
      )) {
        if (value === undefined || value === null) continue;
        url.searchParams.set(key, String(value));
      }
    }
    return url.toString();
  }

  private async request<T>(
    path: string,
    options: RequestOptions = {},
  ): Promise<T> {
    const { method = "GET", body, query, authenticated = true } = options;
    const headers: Record<string, string> = {};
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
    }
    if (authenticated) {
      const token = readToken();
      if (token) headers["Authorization"] = `Bearer ${token}`;
    }

    let response: Response;
    try {
      response = await fetch(this.buildUrl(path, query), {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (cause) {
      throw new NetworkError(
        cause instanceof Error ? cause.message : "network request failed",
      );
    }

    if (response.status === 204) {
      return undefined as T;
    }

    const text = await response.text();
    let parsed: unknown = null;
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = text;
      }
    }

    if (!response.ok) {
      const detail = extractDetail(parsed) ?? response.statusText;
      throw new ApiError(response.status, detail, parsed);
    }

    return parsed as T;
  }
}

function extractDetail(body: unknown): string | null {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    // FastAPI 422 returns detail as a list of validation errors; surface
    // the first message so the UI has something to show.
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0];
      if (first && typeof first === "object" && "msg" in first) {
        const msg = (first as { msg: unknown }).msg;
        if (typeof msg === "string") return msg;
      }
    }
  }
  return null;
}

export const apiClient = new ApiClient();
