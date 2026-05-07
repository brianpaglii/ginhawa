// Single source of truth for date/time formatting in the portal.
//
// We use Intl.DateTimeFormat directly — date-fns / moment are explicitly
// out per the foundation prompt's "no extra deps" rule, and Intl is good
// enough for "May 6, 2026, 7:54 PM"-style display.

const MEDIUM_DATE_SHORT_TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return MEDIUM_DATE_SHORT_TIME.format(d);
}

// "Xm Ys" between two ISO timestamps. Returns null if either bound is
// missing or the math doesn't make sense (negative duration, NaN); the
// caller renders an em-dash in that case.
export function formatDuration(
  start: string | null | undefined,
  end: string | null | undefined,
): string | null {
  if (!start || !end) return null;
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (!Number.isFinite(ms) || ms < 0) return null;
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}m ${s}s`;
}
