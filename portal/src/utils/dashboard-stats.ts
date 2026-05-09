// Pure aggregators for the dashboard. No React, no fetch, no
// Date-mutating side effects on inputs — these are tested directly
// in DashboardPage.test.tsx by exercising the page that calls them,
// and any future unit-level tests can import them as-is.
//
// All "today" / "N days ago" computations use the host's local
// timezone via the Date constructor; the kiosk and the portal are
// expected to run in the same deployment locale (Asia/Manila in
// practice), and the cloud stores ISO-8601 UTC strings which
// Date(s) parses correctly.

import type {
  CitizenRead,
  MeasurementPath,
  SessionRead,
  SessionStatus,
} from "../api/client";

export interface SessionsByDayBucket {
  // YYYY-MM-DD in the local timezone — used as the bar label.
  date: string;
  completed: number;
  aborted: number;
  in_progress: number;
  error: number;
}

export type PathBreakdown = Record<MeasurementPath | "unknown", number>;

// Midnight-this-morning in local time. Used to bucket sessions
// "today" vs everything older. Keeps the day boundary stable
// across calls within the same render.
export function startOfTodayLocal(now: Date = new Date()): Date {
  const d = new Date(now);
  d.setHours(0, 0, 0, 0);
  return d;
}

// N days before midnight-this-morning. Used to bound rolling-window
// counts ("last 7 days", "last 14 days").
export function startOfDaysAgoLocal(
  days: number,
  now: Date = new Date(),
): Date {
  const d = startOfTodayLocal(now);
  d.setDate(d.getDate() - days);
  return d;
}

export function countSessionsBetween(
  sessions: SessionRead[],
  fromDate: Date,
  toDate: Date,
): number {
  const fromMs = fromDate.getTime();
  const toMs = toDate.getTime();
  let count = 0;
  for (const s of sessions) {
    const t = Date.parse(s.started_at);
    if (Number.isNaN(t)) continue;
    if (t >= fromMs && t < toMs) count++;
  }
  return count;
}

export function countCitizensRegisteredSince(
  citizens: CitizenRead[],
  fromDate: Date,
): number {
  const fromMs = fromDate.getTime();
  let count = 0;
  for (const c of citizens) {
    const t = Date.parse(c.registered_at);
    if (Number.isNaN(t)) continue;
    if (t >= fromMs) count++;
  }
  return count;
}

// Returns a bucket per day from (today - days + 1) … today inclusive.
// For days=14, that's 14 buckets ending with today. Empty days have
// all-zero counts so the chart can render without a gap.
export function groupSessionsByDay(
  sessions: SessionRead[],
  days: number = 14,
  now: Date = new Date(),
): SessionsByDayBucket[] {
  const buckets: SessionsByDayBucket[] = [];
  const byDateKey = new Map<string, SessionsByDayBucket>();
  // Pre-populate buckets so empty days appear in the chart.
  for (let i = days - 1; i >= 0; i--) {
    const d = startOfDaysAgoLocal(i, now);
    const key = isoDate(d);
    const bucket: SessionsByDayBucket = {
      date: key,
      completed: 0,
      aborted: 0,
      in_progress: 0,
      error: 0,
    };
    buckets.push(bucket);
    byDateKey.set(key, bucket);
  }
  for (const s of sessions) {
    const t = Date.parse(s.started_at);
    if (Number.isNaN(t)) continue;
    const key = isoDate(new Date(t));
    const bucket = byDateKey.get(key);
    if (!bucket) continue; // outside the window
    incStatus(bucket, s.status);
  }
  return buckets;
}

function incStatus(bucket: SessionsByDayBucket, status: SessionStatus): void {
  // exhaustive switch so TS catches a future SessionStatus addition.
  switch (status) {
    case "completed":
      bucket.completed++;
      return;
    case "aborted":
      bucket.aborted++;
      return;
    case "in_progress":
      bucket.in_progress++;
      return;
    case "error":
      bucket.error++;
      return;
  }
}

export function groupSessionsByPath(sessions: SessionRead[]): PathBreakdown {
  const out: PathBreakdown = {
    vitals: 0,
    anthropometric: 0,
    full: 0,
    unknown: 0,
  };
  for (const s of sessions) {
    if (s.measurement_path === null) {
      out.unknown++;
    } else {
      out[s.measurement_path]++;
    }
  }
  return out;
}

// "Last N sessions" — the SessionsPage server returns DESC by
// started_at, so the dashboard's caller can simply slice the head
// of the same data.
export function recentSessions(
  sessions: SessionRead[],
  limit: number = 5,
): SessionRead[] {
  return [...sessions]
    .sort((a, b) =>
      a.started_at < b.started_at ? 1 : a.started_at > b.started_at ? -1 : 0,
    )
    .slice(0, limit);
}

function isoDate(d: Date): string {
  // Local-timezone YYYY-MM-DD (what bar labels and bucket lookups
  // need to agree on). toISOString() would shift to UTC, which on
  // a UTC+8 host would put a session taken at 01:00 PHT into the
  // wrong bucket.
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}
