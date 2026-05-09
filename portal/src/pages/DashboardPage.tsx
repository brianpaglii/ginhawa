import { useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { MeasurementPath, SessionRead } from "../api/client";
import { SkeletonCard } from "../components/Skeleton";
import { StatusPill } from "../components/StatusPill";
import { useDashboardStats } from "../hooks/useDashboardStats";
import { formatDateTime } from "../lib/datetime";
import {
  countCitizensRegisteredSince,
  countSessionsBetween,
  groupSessionsByDay,
  groupSessionsByPath,
  recentSessions,
  startOfDaysAgoLocal,
  startOfTodayLocal,
  type PathBreakdown,
  type SessionsByDayBucket,
} from "../utils/dashboard-stats";
import styles from "./DashboardPage.module.css";

// Status palette for the stacked bar chart. Mirrors the StatusPill
// colors (defined in components/StatusPill.module.css) so the chart
// reads consistently with status pills elsewhere on the page.
const STATUS_COLOR = {
  completed: "#047857",
  aborted: "#f59e0b",
  in_progress: "#9ca3af",
  error: "#dc2626",
} as const;

// Donut palette for measurement paths. Distinct hues so the slices
// are visually separable; the order matches the legend below the
// chart.
const PATH_COLOR: Record<keyof PathBreakdown, string> = {
  vitals: "#2563eb",
  anthropometric: "#7c3aed",
  full: "#10b981",
  unknown: "#9ca3af",
};

const PATH_LABELS: Record<keyof PathBreakdown, string> = {
  vitals: "Vitals",
  anthropometric: "Anthropometric",
  full: "Full",
  unknown: "Unspecified",
};

const FOURTEEN_DAYS = 14;
const SEVEN_DAYS = 7;
const THIRTY_DAYS = 30;

export function DashboardPage() {
  const { data, isPending, isError } = useDashboardStats();

  const computed = useMemo(() => {
    const now = new Date();
    const todayStart = startOfTodayLocal(now);
    const tomorrow = new Date(todayStart);
    tomorrow.setDate(tomorrow.getDate() + 1);
    const sevenDaysAgo = startOfDaysAgoLocal(SEVEN_DAYS, now);
    const fourteenDaysAgo = startOfDaysAgoLocal(FOURTEEN_DAYS, now);
    const thirtyDaysAgo = startOfDaysAgoLocal(THIRTY_DAYS, now);

    const sessionsToday = countSessionsBetween(
      data.sessions,
      todayStart,
      tomorrow,
    );
    const sessionsThisWeek = countSessionsBetween(
      data.sessions,
      sevenDaysAgo,
      tomorrow,
    );
    const activeCitizens = countCitizensRegisteredSince(
      data.citizens,
      thirtyDaysAgo,
    );
    const recent = recentSessions(data.sessions, 5);

    // Sessions in the last 14 days, used by both the bar chart and
    // the path donut so both charts share the same denominator.
    const sessionsLast14: SessionRead[] = data.sessions.filter((s) => {
      const t = Date.parse(s.started_at);
      if (Number.isNaN(t)) return false;
      return t >= fourteenDaysAgo.getTime() && t < tomorrow.getTime();
    });

    return {
      sessionsToday,
      sessionsThisWeek,
      activeCitizens,
      sessionsByDay: groupSessionsByDay(sessionsLast14, FOURTEEN_DAYS, now),
      pathBreakdown: groupSessionsByPath(sessionsLast14),
      sessionsLast14Total: sessionsLast14.length,
      recent,
    };
  }, [data]);

  if (isPending) {
    return (
      <section>
        <header className={styles.header}>
          <h1 className={styles.title}>Dashboard</h1>
          <p className={styles.subtitle}>
            Recent kiosk activity for your barangay.
          </p>
        </header>
        <SkeletonCard fields={3} />
        <SkeletonCard fields={4} />
      </section>
    );
  }

  if (isError) {
    return (
      <section>
        <header className={styles.header}>
          <h1 className={styles.title}>Dashboard</h1>
        </header>
        <div role="alert" className={styles.error}>
          Failed to load the dashboard. Reload the page to try again.
        </div>
      </section>
    );
  }

  return (
    <section>
      <header className={styles.header}>
        <h1 className={styles.title}>Dashboard</h1>
        <p className={styles.subtitle}>
          Recent kiosk activity for your barangay.
        </p>
      </header>

      <div className={styles.kpiRow}>
        <Kpi label="Sessions today" value={computed.sessionsToday} />
        <Kpi label="Sessions this week" value={computed.sessionsThisWeek} />
        <Kpi label="Active citizens (30d)" value={computed.activeCitizens} />
      </div>

      <SessionsByDayChart buckets={computed.sessionsByDay} />

      <PathDonut breakdown={computed.pathBreakdown} />

      <RecentActivity recent={computed.recent} />
    </section>
  );
}

function Kpi({ label, value }: { label: string; value: number }) {
  return (
    <div className={styles.kpiCard}>
      <div className={styles.kpiValue}>{value}</div>
      <div className={styles.kpiLabel}>{label}</div>
    </div>
  );
}

function SessionsByDayChart({ buckets }: { buckets: SessionsByDayBucket[] }) {
  const total = buckets.reduce(
    (sum, b) => sum + b.completed + b.aborted + b.in_progress + b.error,
    0,
  );
  return (
    <section className={styles.section}>
      <div className={styles.sectionHeader}>
        <h2 className={styles.sectionTitle}>Sessions per day</h2>
        <span className={styles.sectionMeta}>last 14 days</span>
      </div>
      {total === 0 ? (
        <div className={styles.empty}>No sessions in the last 14 days.</div>
      ) : (
        <div className={styles.chartHost}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={buckets}
              margin={{ top: 8, right: 16, left: 0, bottom: 8 }}
            >
              <CartesianGrid stroke="#e5e7eb" vertical={false} />
              <XAxis
                dataKey="date"
                tickFormatter={shortDate}
                fontSize={12}
                stroke="#6b7280"
              />
              <YAxis
                allowDecimals={false}
                fontSize={12}
                stroke="#6b7280"
                width={28}
              />
              <Tooltip
                contentStyle={{ fontSize: "0.85rem" }}
                cursor={{ fill: "rgba(37, 99, 235, 0.06)" }}
              />
              <Bar
                dataKey="completed"
                stackId="status"
                fill={STATUS_COLOR.completed}
                name="Completed"
              />
              <Bar
                dataKey="aborted"
                stackId="status"
                fill={STATUS_COLOR.aborted}
                name="Aborted"
              />
              <Bar
                dataKey="in_progress"
                stackId="status"
                fill={STATUS_COLOR.in_progress}
                name="In progress"
              />
              <Bar
                dataKey="error"
                stackId="status"
                fill={STATUS_COLOR.error}
                name="Error"
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}

function PathDonut({ breakdown }: { breakdown: PathBreakdown }) {
  const entriesAll: { key: keyof PathBreakdown; value: number }[] = [
    { key: "vitals", value: breakdown.vitals },
    { key: "anthropometric", value: breakdown.anthropometric },
    { key: "full", value: breakdown.full },
    { key: "unknown", value: breakdown.unknown },
  ];
  const entries = entriesAll.filter((e) => e.value > 0);
  const total = entries.reduce((sum, e) => sum + e.value, 0);

  return (
    <section className={styles.section}>
      <div className={styles.sectionHeader}>
        <h2 className={styles.sectionTitle}>Path breakdown</h2>
        <span className={styles.sectionMeta}>last 14 days</span>
      </div>
      {total === 0 ? (
        <div className={styles.empty}>
          No measurement paths to summarize yet.
        </div>
      ) : (
        <div className={styles.donutRow}>
          <div className={styles.chartHost}>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={entries as { key: string; value: number }[]}
                  dataKey="value"
                  nameKey="key"
                  innerRadius={50}
                  outerRadius={90}
                  paddingAngle={2}
                >
                  {entries.map((e) => (
                    <Cell key={e.key} fill={PATH_COLOR[e.key]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ fontSize: "0.85rem" }}
                  formatter={(value, _name, item) => {
                    const payload = (item as { payload?: { key?: string } })
                      ?.payload;
                    const k = payload?.key as keyof PathBreakdown | undefined;
                    return [String(value), k ? PATH_LABELS[k] : ""];
                  }}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <ul className={styles.donutLegend}>
            {entries.map((e) => (
              <li key={e.key}>
                <span
                  className={styles.legendSwatch}
                  style={{ background: PATH_COLOR[e.key] }}
                  aria-hidden
                />
                {PATH_LABELS[e.key]}
                <span className={styles.legendCount}>{e.value}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function RecentActivity({ recent }: { recent: SessionRead[] }) {
  const navigate = useNavigate();
  return (
    <section className={styles.section}>
      <div className={styles.sectionHeader}>
        <h2 className={styles.sectionTitle}>Recent sessions</h2>
        <span className={styles.sectionMeta}>last 5</span>
      </div>
      {recent.length === 0 ? (
        <div className={styles.empty}>No sessions yet.</div>
      ) : (
        <table className={styles.recentTable}>
          <thead>
            <tr>
              <th scope="col">Started</th>
              <th scope="col">Citizen</th>
              <th scope="col">Status</th>
              <th scope="col">Path</th>
            </tr>
          </thead>
          <tbody>
            {recent.map((s) => (
              <tr
                key={s.id}
                onClick={() => navigate(`/sessions/${s.id}`)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    navigate(`/sessions/${s.id}`);
                  }
                }}
                tabIndex={0}
                aria-label={`Open session started ${formatDateTime(s.started_at)}`}
              >
                <td>{formatDateTime(s.started_at)}</td>
                <td>{shortId(s.citizen_id)}</td>
                <td>
                  <StatusPill status={s.status} />
                </td>
                <td>{prettyPath(s.measurement_path)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Link to="/sessions" className={styles.viewAll}>
        View all sessions →
      </Link>
    </section>
  );
}

function shortDate(iso: string): string {
  // YYYY-MM-DD → MM/DD for the bar axis. Compact enough that 14
  // labels fit without rotation.
  const [, m, d] = iso.split("-");
  return `${m}/${d}`;
}

function shortId(id: string): string {
  return id.length > 8 ? `${id.slice(0, 8)}…` : id;
}

function prettyPath(path: MeasurementPath | null): string {
  if (path === null) return "—";
  return path;
}
