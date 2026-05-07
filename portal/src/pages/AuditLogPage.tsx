import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import {
  ApiError,
  apiClient,
  NetworkError,
  type ActorType,
  type AuditLogRead,
  type ListAuditLogParams,
  type Page,
} from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Pagination } from "../components/Pagination";
import styles from "./AuditLogPage.module.css";

const PAGE_SIZE = 50;

const ACTOR_TYPES: readonly ActorType[] = [
  "citizen",
  "bhw",
  "system",
  "kiosk",
  "admin",
] as const;

const OBJECT_TYPES = ["session", "citizen", "user", "measurement"] as const;

interface FilterState {
  actorType: ActorType | "";
  actionPrefix: string;
  objectType: string;
  fromDate: string;
  toDate: string;
}

const EMPTY_FILTERS: FilterState = {
  actorType: "",
  actionPrefix: "",
  objectType: "",
  fromDate: "",
  toDate: "",
};

export function AuditLogPage() {
  const { user } = useAuth();
  // Hard route gate: only admin role has audit_log:read scope on the
  // cloud. BHWs / data_viewers reaching this URL directly get the
  // 403 view rather than firing a query that's guaranteed to fail.
  if (user?.role !== "admin") {
    return <Forbidden />;
  }
  return <AuditLogBody />;
}

function Forbidden() {
  return (
    <section className={styles.forbidden}>
      <h1>403 — Admin only</h1>
      <p>The audit log is restricted to administrators.</p>
      <p>
        <Link to="/sessions">← Back to sessions</Link>
      </p>
    </section>
  );
}

function AuditLogBody() {
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  // Debounced action_prefix so each keystroke doesn't fire a fetch.
  const [debouncedActionPrefix, setDebouncedActionPrefix] = useState("");
  useEffect(() => {
    const t = setTimeout(
      () => setDebouncedActionPrefix(filters.actionPrefix),
      250,
    );
    return () => clearTimeout(t);
  }, [filters.actionPrefix]);

  const [page, setPage] = useState(0);

  // Reset to page 0 whenever any filter (other than its own page
  // index) changes, so the user doesn't end up on page 5 of an
  // emptier filtered set.
  const filterSignature = useMemo(
    () =>
      JSON.stringify({
        actorType: filters.actorType,
        actionPrefix: debouncedActionPrefix,
        objectType: filters.objectType,
        fromDate: filters.fromDate,
        toDate: filters.toDate,
      }),
    [
      filters.actorType,
      filters.objectType,
      filters.fromDate,
      filters.toDate,
      debouncedActionPrefix,
    ],
  );
  useEffect(() => {
    setPage(0);
  }, [filterSignature]);

  const queryParams = useMemo<ListAuditLogParams>(() => {
    const params: ListAuditLogParams = {
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    };
    if (filters.actorType) params.actor_type = filters.actorType;
    if (debouncedActionPrefix.trim())
      params.action_prefix = debouncedActionPrefix.trim();
    if (filters.objectType) params.object_type = filters.objectType;
    if (filters.fromDate)
      params.timestamp_after = `${filters.fromDate}T00:00:00`;
    if (filters.toDate)
      params.timestamp_before = `${filters.toDate}T23:59:59.999`;
    return params;
  }, [page, filters, debouncedActionPrefix]);

  const query = useQuery<Page<AuditLogRead>>({
    queryKey: ["audit-log", queryParams],
    queryFn: () => apiClient.listAuditLog(queryParams),
    placeholderData: (previousData) => previousData,
  });

  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  function toggleExpanded(id: number) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const filtersDirty =
    filters.actorType !== "" ||
    filters.actionPrefix !== "" ||
    filters.objectType !== "" ||
    filters.fromDate !== "" ||
    filters.toDate !== "";

  return (
    <section>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>Audit log</h1>
          <p className={styles.subtitle}>
            All audited actions, newest first. Filter by actor, action
            namespace, target, or date range.
          </p>
        </div>
      </header>

      <div className={styles.filters}>
        <div className={styles.filterField}>
          <label className={styles.filterLabel} htmlFor="audit-actor-type">
            Actor type
          </label>
          <select
            id="audit-actor-type"
            className={styles.filterSelect}
            value={filters.actorType}
            onChange={(e) =>
              setFilters((f) => ({
                ...f,
                actorType: e.currentTarget.value as ActorType | "",
              }))
            }
          >
            <option value="">All</option>
            {ACTOR_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>

        <div className={styles.filterField}>
          <label className={styles.filterLabel} htmlFor="audit-action-prefix">
            Action starts with
          </label>
          <input
            id="audit-action-prefix"
            type="search"
            className={styles.filterInput}
            placeholder="e.g. fsm."
            value={filters.actionPrefix}
            onChange={(e) =>
              setFilters((f) => ({
                ...f,
                actionPrefix: e.currentTarget.value,
              }))
            }
          />
        </div>

        <div className={styles.filterField}>
          <label className={styles.filterLabel} htmlFor="audit-object-type">
            Object type
          </label>
          <select
            id="audit-object-type"
            className={styles.filterSelect}
            value={filters.objectType}
            onChange={(e) =>
              setFilters((f) => ({
                ...f,
                objectType: e.currentTarget.value,
              }))
            }
          >
            <option value="">All</option>
            {OBJECT_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>

        <div className={styles.filterField}>
          <label className={styles.filterLabel} htmlFor="audit-from-date">
            From date
          </label>
          <input
            id="audit-from-date"
            type="date"
            className={styles.filterInput}
            value={filters.fromDate}
            onChange={(e) =>
              setFilters((f) => ({ ...f, fromDate: e.currentTarget.value }))
            }
          />
        </div>

        <div className={styles.filterField}>
          <label className={styles.filterLabel} htmlFor="audit-to-date">
            To date
          </label>
          <input
            id="audit-to-date"
            type="date"
            className={styles.filterInput}
            value={filters.toDate}
            onChange={(e) =>
              setFilters((f) => ({ ...f, toDate: e.currentTarget.value }))
            }
          />
        </div>

        <div className={styles.filterActions}>
          <button
            type="button"
            className={styles.clearBtn}
            onClick={() => setFilters(EMPTY_FILTERS)}
            disabled={!filtersDirty}
          >
            Clear filters
          </button>
        </div>
      </div>

      {renderTable(query, expandedIds, toggleExpanded)}

      {query.data && (
        <Pagination
          page={page}
          pageSize={PAGE_SIZE}
          total={query.data.total}
          shown={query.data.items.length}
          busy={query.isFetching}
          onPageChange={setPage}
        />
      )}
    </section>
  );
}

function renderTable(
  query: ReturnType<typeof useQuery<Page<AuditLogRead>>>,
  expandedIds: Set<number>,
  toggleExpanded: (id: number) => void,
) {
  if (query.isPending) {
    return (
      <div role="status" aria-live="polite">
        Loading…
      </div>
    );
  }
  if (query.isError) {
    return (
      <div role="alert" className={styles.error}>
        {messageFor(query.error)}
      </div>
    );
  }
  const data = query.data;
  if (data.items.length === 0) {
    return (
      <div className={styles.empty}>No audit entries match your filters.</div>
    );
  }
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th scope="col">Timestamp</th>
            <th scope="col">Actor</th>
            <th scope="col">Action</th>
            <th scope="col">Object</th>
            <th scope="col">Details</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((entry) => (
            <Row
              key={entry.id}
              entry={entry}
              expanded={expandedIds.has(entry.id)}
              onToggle={() => toggleExpanded(entry.id)}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Row({
  entry,
  expanded,
  onToggle,
}: {
  entry: AuditLogRead;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <tr>
      <td>{formatTimestampWithSeconds(entry.timestamp)}</td>
      <td className={styles.actor}>
        <span className={styles.actorType}>{entry.actor_type}</span>
        {entry.actor_id && (
          <span className={styles.actorId}>{shortId(entry.actor_id)}</span>
        )}
      </td>
      <td className={styles.action}>{entry.action}</td>
      <td>{renderObjectCell(entry)}</td>
      <td className={styles.detailsCell}>
        <DetailsCell
          details={entry.details}
          expanded={expanded}
          onToggle={onToggle}
        />
      </td>
    </tr>
  );
}

function DetailsCell({
  details,
  expanded,
  onToggle,
}: {
  details: string | null;
  expanded: boolean;
  onToggle: () => void;
}) {
  if (details === null) return <>—</>;
  const parsed = tryParseJson(details);
  const preview =
    typeof parsed === "object" && parsed !== null
      ? Object.keys(parsed).slice(0, 3).join(", ")
      : String(parsed).slice(0, 80);
  return (
    <>
      <div className={styles.detailsPreview} title={details}>
        {preview || details}
      </div>
      <button
        type="button"
        className={styles.expandBtn}
        onClick={onToggle}
        aria-expanded={expanded}
      >
        {expanded ? "Collapse" : "Expand"}
      </button>
      {expanded && (
        <pre className={styles.detailsExpanded}>
          {parsed === undefined ? details : JSON.stringify(parsed, null, 2)}
        </pre>
      )}
    </>
  );
}

function renderObjectCell(entry: AuditLogRead) {
  if (!entry.object_type) return "—";
  const id = entry.object_id;
  if (!id) return entry.object_type;

  if (entry.object_type === "session") {
    return (
      <Link className={styles.objectLink} to={`/sessions/${id}`}>
        session {shortId(id)}
      </Link>
    );
  }
  if (entry.object_type === "citizen") {
    return (
      <Link className={styles.objectLink} to={`/citizens/${id}`}>
        citizen {shortId(id)}
      </Link>
    );
  }
  return (
    <span className={styles.action}>
      {entry.object_type} {shortId(id)}
    </span>
  );
}

function formatTimestampWithSeconds(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  // formatDateTime drops seconds; the audit timeline needs them so
  // we can distinguish events within the same minute.
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(d);
}

function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id;
}

function tryParseJson(s: string): unknown {
  try {
    return JSON.parse(s);
  } catch {
    return undefined;
  }
}

function messageFor(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401)
      return "Your session has expired. Please log in again.";
    if (err.status === 403) return "You don’t have access to the audit log.";
    return err.message || `Request failed (${err.status}).`;
  }
  if (err instanceof NetworkError) {
    return "Could not reach the server. Check your connection.";
  }
  return "Something went wrong loading the audit log.";
}
