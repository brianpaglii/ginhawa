import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  ApiError,
  NetworkError,
  type CitizenRead,
  type SessionRead,
  type Sex,
} from "../api/client";
import { Pagination } from "../components/Pagination";
import { SkeletonCard } from "../components/Skeleton";
import { StatusPill } from "../components/StatusPill";
import { useCitizen, useCitizenSessions } from "../hooks/useCitizens";
import { formatDateTime } from "../lib/datetime";
import { computeAge } from "../utils/age";
import styles from "./CitizenDetailPage.module.css";

const SESSION_PAGE_SIZE = 20;

const SEX_LABELS: Record<Sex, string> = {
  M: "Male",
  F: "Female",
  O: "Other",
};

export function CitizenDetailPage() {
  const { id } = useParams<{ id: string }>();
  const citizenId = id ?? "";

  const citizenQuery = useCitizen(citizenId);

  // Render the dedicated 404 view when the citizen lookup confirms
  // the id doesn't exist; this short-circuits the dependent sessions
  // fetch.
  if (
    citizenQuery.error instanceof ApiError &&
    citizenQuery.error.status === 404
  ) {
    return <NotFound />;
  }

  return (
    <CitizenDetailBody citizenId={citizenId} citizenQuery={citizenQuery} />
  );
}

function NotFound() {
  return (
    <section className={styles.notFound}>
      <h1>Citizen not found</h1>
      <p>
        The citizen you’re looking for does not exist or you don’t have access
        to them.
      </p>
      <p>
        <Link to="/citizens">← Back to citizens</Link>
      </p>
    </section>
  );
}

interface BodyProps {
  citizenId: string;
  citizenQuery: ReturnType<typeof useCitizen>;
}

function CitizenDetailBody({ citizenId, citizenQuery }: BodyProps) {
  const [page, setPage] = useState(0);
  const sessionsQuery = useCitizenSessions(citizenId, page, SESSION_PAGE_SIZE);

  return (
    <div>
      <div className={styles.breadcrumb}>
        <Link to="/citizens">← Citizens</Link>
      </div>

      <Header query={citizenQuery} />

      <Stats query={sessionsQuery} />

      <SessionsSection
        query={sessionsQuery}
        page={page}
        onPageChange={setPage}
      />
    </div>
  );
}

function Header({ query }: { query: ReturnType<typeof useCitizen> }) {
  if (query.isPending) {
    return <SkeletonCard fields={6} />;
  }
  if (query.isError) {
    return (
      <section className={styles.section}>
        <div role="alert" className={styles.error}>
          {messageFor(query.error)}
        </div>
      </section>
    );
  }
  const c = query.data;
  return (
    <section className={styles.section}>
      <div className={styles.headerRow}>
        <h1>{c.full_name}</h1>
        <span style={{ color: "#6b7280" }}>{c.barangay}</span>
      </div>
      <div className={styles.fields}>
        <Field label="Age" value={`${computeAge(c.dob)} years`} />
        <Field label="Sex" value={SEX_LABELS[c.sex] ?? c.sex} />
        <Field label="Phone" value={c.phone ?? "—"} />
        <Field label="RFID" value={c.rfid_uid} />
        <Field label="Registered" value={formatDateTime(c.registered_at)} />
        <Field label="Registered by" value={prettyRegisteredBy(c)} />
        <Field
          label="Status"
          value={c.is_active === 1 ? "Active" : "Inactive"}
        />
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

function Stats({ query }: { query: ReturnType<typeof useCitizenSessions> }) {
  if (query.isPending || query.isError) {
    // Don't render an error in the small stats card — the sessions
    // section below carries the same query state and will surface
    // it. Skipping while loading avoids a flash of zeros.
    return null;
  }
  const items = query.data.items;
  const total = query.data.total;
  // Stats here reflect the CURRENT PAGE only when total exceeds it —
  // good enough for v1, where typical citizens have <20 sessions.
  // The "Total sessions" cell uses the Page envelope's `total`
  // (server-side count), which is always accurate.
  const completed = items.filter((s) => s.status === "completed").length;
  const aborted = items.filter((s) => s.status === "aborted").length;
  const errored = items.filter((s) => s.status === "error").length;
  const last = items[0]?.started_at; // server returns DESC

  return (
    <section className={styles.section}>
      <h2 className={styles.sectionHeader}>Activity</h2>
      <div className={styles.statsGrid}>
        <Stat label="Total sessions" value={String(total)} />
        <Stat label="Last session" value={last ? formatDateTime(last) : "—"} />
        <Stat label="Completed" value={String(completed)} />
        <Stat label="Aborted" value={String(aborted)} />
        <Stat label="Errored" value={String(errored)} />
      </div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.stat}>
      <span className={styles.statValue}>{value}</span>
      <span className={styles.statLabel}>{label}</span>
    </div>
  );
}

function SessionsSection({
  query,
  page,
  onPageChange,
}: {
  query: ReturnType<typeof useCitizenSessions>;
  page: number;
  onPageChange: (p: number) => void;
}) {
  const navigate = useNavigate();

  if (query.isPending) {
    return (
      <section className={styles.section} aria-busy="true">
        <h2 className={styles.sectionHeader}>Sessions</h2>
        <div>Loading…</div>
      </section>
    );
  }
  if (query.isError) {
    return (
      <section className={styles.section}>
        <h2 className={styles.sectionHeader}>Sessions</h2>
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
        <h2 className={styles.sectionHeader}>Sessions</h2>
        <div className={styles.empty}>This citizen has no sessions yet.</div>
      </section>
    );
  }

  return (
    <section className={styles.section}>
      <h2 className={styles.sectionHeader}>Sessions</h2>
      <table className={`${styles.table} responsive-table`}>
        <thead>
          <tr>
            <th scope="col">Started</th>
            <th scope="col">Ended</th>
            <th scope="col">Status</th>
            <th scope="col">Path</th>
            <th scope="col">Measurements</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((s) => (
            <Row
              key={s.id}
              session={s}
              onClick={() => navigate(`/sessions/${s.id}`)}
            />
          ))}
        </tbody>
      </table>
      <Pagination
        page={page}
        pageSize={SESSION_PAGE_SIZE}
        total={data.total}
        shown={data.items.length}
        busy={query.isFetching}
        onPageChange={onPageChange}
      />
    </section>
  );
}

function Row({
  session,
  onClick,
}: {
  session: SessionRead;
  onClick: () => void;
}) {
  return (
    <tr
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      tabIndex={0}
      aria-label={`Open session started ${formatDateTime(session.started_at)}`}
    >
      <td data-label="Started">{formatDateTime(session.started_at)}</td>
      <td data-label="Ended">{formatDateTime(session.ended_at)}</td>
      <td data-label="Status">
        <StatusPill status={session.status} />
      </td>
      <td data-label="Path">{session.measurement_path ?? "—"}</td>
      <td data-label="Measurements">{session.measurement_count}</td>
    </tr>
  );
}

function prettyRegisteredBy(c: CitizenRead): string {
  if (c.registered_by === null) return "Self-service";
  if (c.registered_by === "seed_script") return "Seed data";
  // The cloud stores the BHW user id; resolving to a name would
  // require a /users lookup that admins have but BHWs don't. For
  // v1 we surface the id; a future prompt can swap in the name.
  return c.registered_by;
}

function messageFor(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401)
      return "Your session has expired. Please log in again.";
    if (err.status === 404) return "Not found.";
    return err.message || `Request failed (${err.status}).`;
  }
  if (err instanceof NetworkError) {
    return "Could not reach the server. Check your connection.";
  }
  return "Something went wrong loading this data.";
}
