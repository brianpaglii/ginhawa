import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import {
  ApiError,
  apiClient,
  NetworkError,
  type CitizenRead,
  type Page,
  type SessionRead,
  type SessionStatus,
} from "../api/client";
import { formatDateTime } from "../lib/datetime";
import styles from "./SessionsPage.module.css";

const PAGE_SIZE = 20;

// Citizens change rarely compared to sessions; cache for 5 min so
// switching session pages doesn't refetch the citizen map.
const CITIZENS_STALE_MS = 5 * 60_000;

const STATUS_LABELS: Record<SessionStatus, string> = {
  in_progress: "In progress",
  completed: "Completed",
  aborted: "Aborted",
  error: "Error",
};

const STATUS_PILL_CLASS: Record<SessionStatus, string> = {
  in_progress: styles.statusInProgress,
  completed: styles.statusCompleted,
  aborted: styles.statusAborted,
  error: styles.statusError,
};

export function SessionsPage() {
  const [page, setPage] = useState(0);
  const navigate = useNavigate();

  const sessionsQuery = useQuery<Page<SessionRead>>({
    queryKey: ["sessions", page],
    queryFn: () =>
      apiClient.listSessions({ limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    placeholderData: (previousData) => previousData,
  });

  // Fetch citizens once and build an id → name map. We intentionally
  // request the API's max page size (200); v1 BHW deployments are
  // barangay-scoped (a few hundred citizens at most), and admins
  // viewing a larger pool get name lookups for the first 200 — beyond
  // that, a row falls back to the citizen_id (truncated). A second
  // pass with offset would be a Prompt-4 concern.
  const citizensQuery = useQuery<Page<CitizenRead>>({
    queryKey: ["citizens"],
    queryFn: () => apiClient.listCitizens({ limit: 200, is_active: true }),
    staleTime: CITIZENS_STALE_MS,
  });

  const citizensById = new Map<string, CitizenRead>();
  for (const c of citizensQuery.data?.items ?? []) {
    citizensById.set(c.id, c);
  }

  if (sessionsQuery.isPending) {
    return (
      <div role="status" aria-live="polite">
        Loading…
      </div>
    );
  }

  if (sessionsQuery.isError) {
    return (
      <div role="alert" className={styles.error}>
        {messageFor(sessionsQuery.error)}
      </div>
    );
  }

  const data = sessionsQuery.data;
  const total = data.total;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const showingFrom = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const showingTo = Math.min(total, page * PAGE_SIZE + data.items.length);

  return (
    <section>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>Sessions</h1>
          <p className={styles.subtitle}>
            Kiosk sessions in your barangay, newest first.
          </p>
        </div>
      </header>

      {data.items.length === 0 ? (
        <div className={styles.empty}>No sessions yet.</div>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th scope="col">Started</th>
                <th scope="col">Citizen</th>
                <th scope="col">Status</th>
                <th scope="col">Path</th>
                <th scope="col">Measurements</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((session) => {
                const citizen = citizensById.get(session.citizen_id);
                const citizenLabel =
                  citizen?.full_name ||
                  citizen?.rfid_uid ||
                  shortId(session.citizen_id);
                return (
                  <tr
                    key={session.id}
                    onClick={() => navigate(`/sessions/${session.id}`)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        navigate(`/sessions/${session.id}`);
                      }
                    }}
                    tabIndex={0}
                    aria-label={`Open session ${shortId(session.id)} for ${citizenLabel}`}
                  >
                    <td>{formatDateTime(session.started_at)}</td>
                    <td>{citizenLabel}</td>
                    <td>
                      <span
                        className={`${styles.statusPill} ${STATUS_PILL_CLASS[session.status]}`}
                      >
                        {STATUS_LABELS[session.status]}
                      </span>
                    </td>
                    <td>{session.measurement_path ?? "—"}</td>
                    <td>{session.measurement_count}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {total > 0 && (
        <div className={styles.pagination}>
          <span>
            Showing {showingFrom}–{showingTo} of {total}
          </span>
          <div className={styles.pageBtns}>
            <button
              type="button"
              className={styles.btn}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0 || sessionsQuery.isFetching}
            >
              Previous
            </button>
            <span aria-live="polite">
              Page {page + 1} of {totalPages}
            </span>
            <button
              type="button"
              className={styles.btn}
              onClick={() => setPage((p) => p + 1)}
              disabled={page + 1 >= totalPages || sessionsQuery.isFetching}
            >
              Next
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function shortId(id: string): string {
  return id.length > 8 ? `${id.slice(0, 8)}…` : id;
}

function messageFor(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401)
      return "Your session has expired. Please log in again.";
    return err.message || `Request failed (${err.status}).`;
  }
  if (err instanceof NetworkError) {
    return "Could not reach the server. Check your connection.";
  }
  return "Something went wrong loading sessions.";
}
