import { Navigate, Route, Routes } from "react-router-dom";

import { useAuth } from "./auth/AuthContext";
import { AppLayout } from "./layouts/AppLayout";
import { LoginPage } from "./pages/LoginPage";
import { SessionDetailPage } from "./pages/SessionDetailPage";
import { SessionsPage } from "./pages/SessionsPage";
import { ProtectedRoute } from "./routes/ProtectedRoute";

function RootRedirect() {
  const { status } = useAuth();
  if (status === "authenticating") {
    return (
      <div role="status" aria-live="polite" style={{ padding: "2rem" }}>
        Loading…
      </div>
    );
  }
  return (
    <Navigate
      to={status === "authenticated" ? "/sessions" : "/login"}
      replace
    />
  );
}

function App() {
  return (
    <Routes>
      <Route path="/" element={<RootRedirect />} />
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<AppLayout />}>
          <Route path="/sessions" element={<SessionsPage />} />
          <Route path="/sessions/:id" element={<SessionDetailPage />} />
          {/* /dashboard preserved as a redirect for any bookmarks */}
          <Route
            path="/dashboard"
            element={<Navigate to="/sessions" replace />}
          />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;
