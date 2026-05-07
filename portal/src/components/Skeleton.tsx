import styles from "./Skeleton.module.css";

interface BarProps {
  width?: string;
  height?: string;
  style?: React.CSSProperties;
}

// Inline shimmering placeholder. Width/height accept any CSS length;
// pages that need a specific shape pass them in.
export function SkeletonBar({
  width = "100%",
  height = "1rem",
  style,
}: BarProps) {
  return (
    <span
      aria-hidden
      className={styles.bar}
      style={{ width, height, ...style }}
    />
  );
}

interface TableProps {
  columns: number;
  rows?: number;
}

// Standalone "loading rows" panel that mimics a table's silhouette
// without using a real <table>/<tr> structure. The accessibility
// role on this panel is "status" so screen readers announce
// loading; the surrounding shell stays out of the role="table"
// query so tests can wait for the real data table without racing
// the loading state.
export function SkeletonTable({ columns, rows = 5 }: TableProps) {
  const colsStyle = {
    "--cols": columns,
  } as React.CSSProperties;
  return (
    <div
      role="status"
      aria-live="polite"
      aria-label="Loading rows"
      className={styles.tableShell}
    >
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className={styles.tableRow} style={colsStyle}>
          {Array.from({ length: columns }).map((__, c) => (
            <SkeletonBar
              key={c}
              height="0.95rem"
              width={c === 0 ? "70%" : "60%"}
              style={{ height: "0.95rem" }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

interface CardProps {
  fields?: number;
}

// Header / detail card with a wide title bar and a grid of stub
// fields. Used by the session-detail and citizen-detail header
// sections.
export function SkeletonCard({ fields = 6 }: CardProps) {
  return (
    <section
      className={styles.cardSection}
      role="status"
      aria-live="polite"
      aria-label="Loading details"
    >
      <SkeletonBar
        height="1.4rem"
        width="35%"
        style={{ marginBottom: "0.85rem" }}
      />
      <div className={styles.fieldGrid}>
        {Array.from({ length: fields }).map((_, i) => (
          <div key={i} className={styles.fieldBlock}>
            <SkeletonBar width="40%" height="0.7rem" />
            <SkeletonBar width="80%" height="1rem" />
          </div>
        ))}
      </div>
    </section>
  );
}
