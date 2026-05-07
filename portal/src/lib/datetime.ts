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
