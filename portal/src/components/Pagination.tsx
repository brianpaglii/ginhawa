import styles from "./Pagination.module.css";

interface Props {
  // Zero-based page index.
  page: number;
  pageSize: number;
  total: number;
  // True when the next page is being fetched in the background.
  busy?: boolean;
  // Number of rows currently displayed (for the "Showing X–Y of N"
  // copy). Caller passes this rather than recomputing because pages
  // may be partially full at the tail.
  shown: number;
  onPageChange: (page: number) => void;
}

export function Pagination({
  page,
  pageSize,
  total,
  busy = false,
  shown,
  onPageChange,
}: Props) {
  if (total === 0) return null;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const from = page * pageSize + 1;
  const to = Math.min(total, page * pageSize + shown);
  return (
    <div className={styles.bar}>
      <span>
        Showing {from}–{to} of {total}
      </span>
      <div className={styles.btns}>
        <button
          type="button"
          className={styles.btn}
          onClick={() => onPageChange(Math.max(0, page - 1))}
          disabled={page === 0 || busy}
        >
          Previous
        </button>
        <span aria-live="polite">
          Page {page + 1} of {totalPages}
        </span>
        <button
          type="button"
          className={styles.btn}
          onClick={() => onPageChange(page + 1)}
          disabled={page + 1 >= totalPages || busy}
        >
          Next
        </button>
      </div>
    </div>
  );
}
