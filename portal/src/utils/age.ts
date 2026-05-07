// Years between an ISO date-of-birth and "now" (or a caller-supplied
// reference date). Returns 0 for malformed input rather than NaN —
// the UI renders the value verbatim and "NaN years" is worse than
// silently bottoming out at zero. Caller should still null-check the
// dob string before calling.

export function computeAge(
  dobIso: string,
  reference: Date = new Date(),
): number {
  const dob = new Date(dobIso);
  if (Number.isNaN(dob.getTime())) return 0;
  let age = reference.getFullYear() - dob.getFullYear();
  const m = reference.getMonth() - dob.getMonth();
  if (m < 0 || (m === 0 && reference.getDate() < dob.getDate())) {
    age -= 1;
  }
  return Math.max(0, age);
}
