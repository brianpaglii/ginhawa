import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "../auth/use-auth";

export function ProtectedRoute() {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "authenticating") {
    return (
      <div role="status" aria-live="polite" style={{ padding: "2rem" }}>
        Loading…
      </div>
    );
  }

  if (status === "unauthenticated") {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <Outlet />;
}
