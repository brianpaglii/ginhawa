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
