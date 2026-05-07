import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import {
  ApiError,
  apiClient,
  NetworkError,
  type AuditLogRead,
  type CitizenRead,
  type MeasurementRead,
  type Page,
  type SessionRead,
} from "../api/client";
import { useAuth } from "../auth/use-auth";
import { SkeletonCard } from "../components/Skeleton";
import { StatusPill } from "../components/StatusPill";
import { formatDateTime, formatDuration } from "../lib/datetime";
import styles from "./SessionDetailPage.module.css";

const AUDIT_PAGE_SIZE = 50;

export function SessionDetailPage() {
  const { id } = useParams<{ id: string }>();
  const sessionId = id ?? "";

  const sessionQuery = useQuery<SessionRead, unknown>({
    queryKey: ["session", sessionId],
    queryFn: () => apiClient.getSession(sessionId),
    enabled: sessionId !== "",
    retry: (failureCount, err) => {
      if (err instanceof ApiError && err.status === 404) return false;
      return failureCount < 1;
    },
  });

  // Render the 404 view explicitly when the session lookup returns
  // 404 — early-return so we don't fire the dependent queries.
  if (
    sessionQuery.error instanceof ApiError &&
    sessionQuery.error.status === 404
  ) {
    return <NotFound />;
  }

  return (
    <SessionDetailBody sessionId={sessionId} sessionQuery={sessionQuery} />
  );
}

function NotFound() {
  return (
    <section className={styles.notFound}>
      <h1>Session not found</h1>
      <p>
        The session you’re looking for does not exist or you don’t have access
        to it.
      </p>
      <p>
        <Link to="/sessions">← Back to sessions</Link>
      </p>
    </section>
  );
}

interface BodyProps {
  sessionId: string;
  sessionQuery: ReturnType<typeof useQuery<SessionRead, unknown>>;
}

function SessionDetailBody({ sessionId, sessionQuery }: BodyProps) {
  const { user } = useAuth();
  const canReadAudit = user?.role === "admin";

  const session = sessionQuery.data;
  const citizenId = session?.citizen_id;

  const citizenQuery = useQuery<CitizenRead, unknown>({
    queryKey: ["citizen", citizenId],
    queryFn: () => apiClient.getCitizen(citizenId!),
    enabled: !!citizenId,
  });

  // Two parallel measurement queries — one for valid, one for
  // invalidated. The cloud's /measurements default filters to
  // is_valid=true and there's no "any" mode (changing that surprised
  // an existing test pinning the default), so we fetch both and merge
  // client-side. Bench sessions are small (<20 measurements) so the
  // double round-trip is unnoticeable.
  const validQ = useQuery<Page<MeasurementRead>, unknown>({
    queryKey: ["measurements", sessionId, "valid"],
    queryFn: () =>
      apiClient.listMeasurements({
        session_id: sessionId,
        is_valid: true,
        limit: 200,
      }),
    enabled: !!sessionId,
  });
  const invalidQ = useQuery<Page<MeasurementRead>, unknown>({
    queryKey: ["measurements", sessionId, "invalid"],
    queryFn: () =>
      apiClient.listMeasurements({
        session_id: sessionId,
        is_valid: false,
        limit: 200,
      }),
    enabled: !!sessionId,
  });

  const [auditOffset, setAuditOffset] = useState(0);
  const auditQuery = useQuery<Page<AuditLogRead>, unknown>({
    queryKey: ["audit", sessionId, auditOffset],
    queryFn: () =>
      apiClient.listAuditLog({
        object_type: "session",
        object_id: sessionId,
        limit: AUDIT_PAGE_SIZE,
        offset: auditOffset,
      }),
    enabled: !!sessionId && canReadAudit,
  });

  return (
    <div>
      <div className={styles.breadcrumb}>
        <Link to="/sessions">← Sessions</Link>
      </div>

      <SessionHeader sessionQuery={sessionQuery} citizenQuery={citizenQuery} />

      <Measurements valid={validQ} invalid={invalidQ} />

      {canReadAudit && (
        <AuditTimeline
          query={auditQuery}
          onShowMore={() => setAuditOffset((o) => o + AUDIT_PAGE_SIZE)}
        />
      )}
    </div>
  );
}

function SessionHeader({
  sessionQuery,
  citizenQuery,
}: {
  sessionQuery: ReturnType<typeof useQuery<SessionRead, unknown>>;
  citizenQuery: ReturnType<typeof useQuery<CitizenRead, unknown>>;
}) {
  if (sessionQuery.isPending) {
    return <SkeletonCard fields={8} />;
  }
  if (sessionQuery.isError) {
    return (
      <section className={styles.section}>
        <div role="alert" className={styles.error}>
          {messageFor(sessionQuery.error)}
        </div>
      </section>
    );
  }
  const session = sessionQuery.data;
  const citizen = citizenQuery.data;
  const citizenName = citizen?.full_name ?? "—";
  const rfid = citizen?.rfid_uid ?? "—";
  const duration = formatDuration(session.started_at, session.ended_at);

  return (
    <section className={styles.section}>
      <div className={styles.headerRow}>
        <h1>Session</h1>
        <StatusPill status={session.status} />
      </div>
      <div className={styles.fields}>
        <Field label="Citizen" value={citizenName} />
        <Field label="RFID" value={rfid} />
        <Field label="Path" value={session.measurement_path ?? "—"} />
        <Field label="Started" value={formatDateTime(session.started_at)} />
        <Field label="Ended" value={formatDateTime(session.ended_at)} />
        <Field label="Duration" value={duration ?? "—"} />
        <Field label="Device" value={session.device_id} />
        <Field
          label="Print"
          value={prettyPrintedStatus(session.printed_status)}
        />
        {session.error_reason !== null && (
          <Field label="Error reason" value={session.error_reason} />
        )}
      </div>
    </section>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.field}>
      <span className={styles.fieldLabel}>{label}</span>
      <span className={styles.fieldValue}>{value}</span>
    </div>
  );
}

function Measurements({
  valid,
  invalid,
}: {
  valid: ReturnType<typeof useQuery<Page<MeasurementRead>, unknown>>;
  invalid: ReturnType<typeof useQuery<Page<MeasurementRead>, unknown>>;
}) {
  const isLoading = valid.isPending || invalid.isPending;
  const error = valid.error ?? invalid.error;

  if (isLoading) {
    return (
      <section className={styles.section} aria-busy="true">
        <h2 className={styles.sectionHeader}>Measurements</h2>
        <div>Loading…</div>
      </section>
    );
  }
  if (error) {
    return (
      <section className={styles.section}>
        <h2 className={styles.sectionHeader}>Measurements</h2>
        <div role="alert" className={styles.error}>
          {messageFor(error)}
        </div>
      </section>
    );
  }

  // Merge + sort oldest-first. Within a measurement type (e.g.,
  // multiple BP attempts within one session) we want chronological
  // order so the BHW can see retries.
  const merged: MeasurementRead[] = [
    ...(valid.data?.items ?? []),
    ...(invalid.data?.items ?? []),
  ].sort((a, b) =>
    a.measured_at < b.measured_at ? -1 : a.measured_at > b.measured_at ? 1 : 0,
  );

  if (merged.length === 0) {
    return (
      <section className={styles.section}>
        <h2 className={styles.sectionHeader}>Measurements</h2>
        <div className={styles.empty}>No measurements captured.</div>
      </section>
    );
  }

  return (
    <section className={styles.section}>
      <h2 className={styles.sectionHeader}>Measurements</h2>
      <table className={`${styles.table} responsive-table`}>
        <thead>
          <tr>
            <th scope="col">Type</th>
            <th scope="col">Value</th>
            <th scope="col">Valid</th>
            <th scope="col">Measured</th>
            <th scope="col">Source</th>
          </tr>
        </thead>
        <tbody>
          {merged.map((m) => (
            <tr key={m.id}>
              <td data-label="Type">{prettyType(m.type)}</td>
              <td data-label="Value">
                {m.value} {m.unit}
              </td>
              <td data-label="Valid">
                {m.is_valid === 1 ? (
                  <span
                    className={styles.validIcon}
                    aria-label="valid measurement"
                  >
                    ✓
                  </span>
                ) : (
                  <span
                    className={styles.invalidIcon}
                    aria-label="invalidated measurement"
                  >
                    ✗
                  </span>
                )}
                {m.is_valid !== 1 && m.validation_notes && (
                  <div className={styles.invalidNote}>{m.validation_notes}</div>
                )}
              </td>
              <td data-label="Measured">{formatDateTime(m.measured_at)}</td>
              <td data-label="Source">{m.source_device}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function AuditTimeline({
  query,
  onShowMore,
}: {
  query: ReturnType<typeof useQuery<Page<AuditLogRead>, unknown>>;
  onShowMore: () => void;
}) {
  if (query.isPending) {
    return (
      <section className={styles.section} aria-busy="true">
        <h2 className={styles.sectionHeader}>Audit log</h2>
        <div>Loading…</div>
      </section>
    );
  }
  if (query.isError) {
    return (
      <section className={styles.section}>
        <h2 className={styles.sectionHeader}>Audit log</h2>
        <div role="alert" className={styles.error}>
          {messageFor(query.error)}
        </div>
      </section>
    );
  }
  const data = query.data;
  if (data.items.length === 0) {
    return (
      <section className={styles.section}>
        <h2 className={styles.sectionHeader}>Audit log</h2>
        <div className={styles.empty}>No audit entries for this session.</div>
      </section>
    );
  }

  // Server returns DESC by timestamp. Reverse for the timeline view —
  // a chronological narrative reads naturally oldest → newest.
  const ordered = [...data.items].reverse();
  const hasMore = data.total > data.items.length;

  return (
    <section className={styles.section}>
      <h2 className={styles.sectionHeader}>Audit log</h2>
      <ol className={styles.timeline}>
        {ordered.map((entry) => (
          <li key={entry.id}>
            <span className={styles.timeTs}>
              {formatDateTime(entry.timestamp)}
            </span>
            <div>
              <div>
                <span className={styles.timeAction}>{entry.action}</span>
                <span className={styles.timeActor}>
                  by {entry.actor_type}
                  {entry.actor_id ? ` (${shortId(entry.actor_id)})` : ""}
                </span>
              </div>
              {renderDetails(entry.details)}
            </div>
          </li>
        ))}
      </ol>
      {hasMore && (
        <button
          type="button"
          className={styles.showMore}
          onClick={onShowMore}
          disabled={query.isFetching}
        >
          {query.isFetching ? "Loading…" : "Show more"}
        </button>
      )}
    </section>
  );
}

function renderDetails(details: string | null): React.ReactNode {
  if (!details) return null;
  // record_audit serializes the details dict to JSON. Try to parse
  // and pretty-print; fall back to the raw string if it isn't JSON.
  try {
    const parsed: unknown = JSON.parse(details);
    return (
      <div className={styles.timeDetails}>
        {JSON.stringify(parsed, null, 2)}
      </div>
    );
  } catch {
    return <div className={styles.timeDetails}>{details}</div>;
  }
}

const TYPE_LABELS: Record<string, string> = {
  systolic_bp: "Systolic BP",
  diastolic_bp: "Diastolic BP",
  spo2: "SpO₂",
  heart_rate: "Heart rate",
  temperature: "Temperature",
  height: "Height",
  weight: "Weight",
  bmi: "BMI",
};

function prettyType(t: string): string {
  return TYPE_LABELS[t] ?? t;
}

function prettyPrintedStatus(s: string): string {
  switch (s) {
    case "not_requested":
      return "Not requested";
    case "printed_ok":
      return "Printed";
    case "paper_out_pre":
      return "Paper out (before print)";
    case "paper_out_mid":
      return "Paper out (mid print)";
    case "print_failed":
      return "Print failed";
    default:
      return s;
  }
}

function shortId(id: string): string {
  return id.length > 8 ? `${id.slice(0, 8)}…` : id;
}

function messageFor(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401)
      return "Your session has expired. Please log in again.";
    if (err.status === 403) return "You don’t have access to this data.";
    return err.message || `Request failed (${err.status}).`;
  }
  if (err instanceof NetworkError) {
    return "Could not reach the server. Check your connection.";
  }
  return "Something went wrong loading this data.";
}
