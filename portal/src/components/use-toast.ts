import { useContext } from "react";

import { ToastContext, type ToastApi } from "./toast-context";

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (ctx === null) {
    throw new Error("useToast must be used within a <ToastProvider>");
  }
  return ctx;
}
