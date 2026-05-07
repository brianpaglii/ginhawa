// Toast context object + types. Lives in a non-component module so
// Toast.tsx can satisfy the react-refresh "only-export-components"
// rule.

import { createContext } from "react";

export type ToastVariant = "error" | "success" | "info";

export interface ToastOptions {
  title: string;
  message?: string;
}

export interface ToastApi {
  toast: (variant: ToastVariant, opts: ToastOptions) => void;
  error: (opts: ToastOptions) => void;
  success: (opts: ToastOptions) => void;
  info: (opts: ToastOptions) => void;
  dismiss: (id: number) => void;
}

export const ToastContext = createContext<ToastApi | null>(null);
