import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  ToastContext,
  type ToastApi,
  type ToastOptions,
  type ToastVariant,
} from "./toast-context";
import styles from "./Toast.module.css";

interface Toast {
  id: number;
  variant: ToastVariant;
  title: string;
  message?: string;
}

const DEFAULT_AUTO_DISMISS_MS = 5000;

interface ProviderProps {
  children: ReactNode;
  autoDismissMs?: number;
}

export function ToastProvider({
  children,
  autoDismissMs = DEFAULT_AUTO_DISMISS_MS,
}: ProviderProps) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback(
    (variant: ToastVariant, opts: ToastOptions) => {
      const id = nextId.current++;
      setToasts((prev) => [...prev, { id, variant, ...opts }]);
      // The interval-clear is handled per-toast in <ToastItem>; we
      // could centralise here, but per-item lets each toast pause if
      // we ever add hover-to-pause behaviour.
      void autoDismissMs;
    },
    [autoDismissMs],
  );

  const api = useMemo<ToastApi>(
    () => ({
      toast,
      error: (opts) => toast("error", opts),
      success: (opts) => toast("success", opts),
      info: (opts) => toast("info", opts),
      dismiss,
    }),
    [toast, dismiss],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className={styles.region} aria-live="polite" aria-atomic="false">
        {toasts.map((t) => (
          <ToastItem
            key={t.id}
            toast={t}
            autoDismissMs={autoDismissMs}
            onDismiss={() => dismiss(t.id)}
          />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastItem({
  toast,
  autoDismissMs,
  onDismiss,
}: {
  toast: Toast;
  autoDismissMs: number;
  onDismiss: () => void;
}) {
  useEffect(() => {
    const t = window.setTimeout(onDismiss, autoDismissMs);
    return () => window.clearTimeout(t);
  }, [onDismiss, autoDismissMs]);

  return (
    <div
      role={toast.variant === "error" ? "alert" : "status"}
      className={`${styles.toast} ${styles[toast.variant]}`}
    >
      <ToastIcon variant={toast.variant} />
      <div className={styles.body}>
        <p className={styles.title}>{toast.title}</p>
        {toast.message && <p className={styles.message}>{toast.message}</p>}
      </div>
      <button
        type="button"
        className={styles.dismiss}
        onClick={onDismiss}
        aria-label="Dismiss notification"
      >
        ×
      </button>
    </div>
  );
}

function ToastIcon({ variant }: { variant: ToastVariant }) {
  const path =
    variant === "error" ? (
      <>
        <circle cx="12" cy="12" r="10" />
        <line x1="12" y1="8" x2="12" y2="12" />
        <line x1="12" y1="16" x2="12.01" y2="16" />
      </>
    ) : variant === "success" ? (
      <>
        <circle cx="12" cy="12" r="10" />
        <polyline points="9 12 11 14 15 10" />
      </>
    ) : (
      <>
        <circle cx="12" cy="12" r="10" />
        <line x1="12" y1="16" x2="12" y2="12" />
        <line x1="12" y1="8" x2="12.01" y2="8" />
      </>
    );
  return (
    <svg
      className={styles.icon}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      {path}
    </svg>
  );
}
