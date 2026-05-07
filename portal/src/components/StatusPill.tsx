import type { SessionStatus } from "../api/client";
import styles from "./StatusPill.module.css";

const LABELS: Record<SessionStatus, string> = {
  in_progress: "In progress",
  completed: "Completed",
  aborted: "Aborted",
  error: "Error",
};

interface Props {
  status: SessionStatus;
}

export function StatusPill({ status }: Props) {
  return (
    <span className={`${styles.pill} ${styles[status]}`}>{LABELS[status]}</span>
  );
}
