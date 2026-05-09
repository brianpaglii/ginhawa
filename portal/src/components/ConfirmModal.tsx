import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import styles from "./ConfirmModal.module.css";

interface ConfirmModalProps {
  open: boolean;
  title: string;
  body: ReactNode;
  onConfirm: (reason?: string) => void | Promise<void>;
  onCancel: () => void;
  confirmText?: string;
  cancelText?: string;
  requireReason?: boolean;
  reasonMinLength?: number;
}

// Focusable selectors used to find the first/last focusable element
// inside the dialog for the Tab/Shift-Tab cycle. Excludes hidden
// elements and inputs marked tabindex="-1".
const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function ConfirmModal({
  open,
  title,
  body,
  onConfirm,
  onCancel,
  confirmText = "Confirm",
  cancelText = "Cancel",
  requireReason = false,
  reasonMinLength = 5,
}: ConfirmModalProps) {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const lastFocusedRef = useRef<HTMLElement | null>(null);
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState(false);
  // Reset transient state every time the modal re-opens, using the
  // "adjust state during render" pattern: an effect would also work,
  // but the lint rule (react-hooks/set-state-in-effect) prefers the
  // synchronous form because navigation isn't an external system.
  const [prevOpen, setPrevOpen] = useState(open);
  if (prevOpen !== open) {
    setPrevOpen(open);
    if (open) {
      setReason("");
      setPending(false);
    }
  }
  const titleId = useId();
  const reasonId = useId();

  // Save the previously-focused element on open, restore it on close
  // so keyboard users land back where they triggered the modal.
  useEffect(() => {
    if (!open) return;
    lastFocusedRef.current = document.activeElement as HTMLElement | null;
    // Defer one tick so the dialog has actually mounted before we
    // try to focus into it.
    const t = window.setTimeout(() => {
      const root = dialogRef.current;
      if (root === null) return;
      const focusables = root.querySelectorAll<HTMLElement>(FOCUSABLE);
      if (focusables.length > 0) {
        focusables[0].focus();
      } else {
        root.focus();
      }
    }, 0);
    return () => {
      window.clearTimeout(t);
      const prior = lastFocusedRef.current;
      if (prior !== null && typeof prior.focus === "function") {
        prior.focus();
      }
    };
  }, [open]);

  const close = useCallback(() => {
    if (pending) return;
    onCancel();
  }, [onCancel, pending]);

  const reasonOk = !requireReason || reason.trim().length >= reasonMinLength;

  async function handleConfirm() {
    if (!reasonOk || pending) return;
    setPending(true);
    try {
      await onConfirm(requireReason ? reason.trim() : undefined);
    } finally {
      setPending(false);
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      e.stopPropagation();
      close();
      return;
    }
    if (e.key !== "Tab") return;
    // Trap focus inside the dialog so it can't escape into the
    // (now visually hidden) page behind.
    const root = dialogRef.current;
    if (root === null) return;
    const focusables = Array.from(
      root.querySelectorAll<HTMLElement>(FOCUSABLE),
    );
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement as HTMLElement | null;
    if (e.shiftKey && active === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }

  if (!open) return null;

  // Portal target: the app's #modal-root if present, otherwise body.
  // The fallback keeps tests that don't inject a #modal-root working
  // — production HTML always has one.
  const target =
    (typeof document !== "undefined" &&
      document.getElementById("modal-root")) ||
    (typeof document !== "undefined" ? document.body : null);
  if (target === null) return null;

  return createPortal(
    <div
      className={styles.backdrop}
      onMouseDown={(e) => {
        // Click on the backdrop closes; clicks inside the dialog
        // bubble through but stop here.
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={styles.dialog}
        onKeyDown={handleKeyDown}
      >
        <h2 id={titleId} className={styles.title}>
          {title}
        </h2>
        <div className={styles.body}>{body}</div>
        {requireReason && (
          <div className={styles.reasonRow}>
            <label htmlFor={reasonId} className={styles.reasonLabel}>
              Reason
            </label>
            <textarea
              id={reasonId}
              className={styles.reasonInput}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              minLength={reasonMinLength}
              required
              placeholder="Why is this being invalidated?"
            />
            <div className={styles.reasonHelp}>
              At least {reasonMinLength} characters.
            </div>
          </div>
        )}
        <div className={styles.actions}>
          <button
            type="button"
            className={styles.cancel}
            onClick={close}
            disabled={pending}
          >
            {cancelText}
          </button>
          <button
            type="button"
            className={styles.confirm}
            onClick={handleConfirm}
            disabled={!reasonOk || pending}
          >
            {pending ? "Loading…" : confirmText}
          </button>
        </div>
      </div>
    </div>,
    target,
  );
}
