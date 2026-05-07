import type { ReactNode } from "react";

import styles from "./EmptyState.module.css";

interface Props {
  icon: ReactNode;
  title: string;
  message: string;
}

// Shared "nothing to show here" panel for list pages. Inline-SVG
// icon (no icon library), short title, and a one-sentence
// explanation of what would populate this list. Pages compose the
// icon themselves so each list can have a distinct shape.
export function EmptyState({ icon, title, message }: Props) {
  return (
    <div className={styles.shell}>
      <div className={styles.icon} aria-hidden>
        {icon}
      </div>
      <h2 className={styles.title}>{title}</h2>
      <p className={styles.message}>{message}</p>
    </div>
  );
}

// A small library of inline-SVG icons used by the empty states. The
// SVGs follow the lucide.dev visual language (24px viewBox, 2px
// stroke, round caps) so adding a real icon set later swaps cleanly.

export const SessionsEmptyIcon = (
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    width="56"
    height="56"
  >
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M3 10h18" />
    <path d="M9 14h6" />
  </svg>
);

export const CitizensEmptyIcon = (
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    width="56"
    height="56"
  >
    <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
    <circle cx="9" cy="7" r="4" />
    <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
    <path d="M16 3.13a4 4 0 0 1 0 7.75" />
  </svg>
);

export const AuditEmptyIcon = (
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    width="56"
    height="56"
  >
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <polyline points="14 2 14 8 20 8" />
    <line x1="9" y1="13" x2="15" y2="13" />
    <line x1="9" y1="17" x2="15" y2="17" />
  </svg>
);
