import { useState } from "react";
import { Outlet, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import styles from "./AppLayout.module.css";

export function AppLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [loggingOut, setLoggingOut] = useState(false);

  async function onLogout() {
    setLoggingOut(true);
    try {
      await logout();
      navigate("/login", { replace: true });
    } finally {
      setLoggingOut(false);
    }
  }

  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <span className={styles.brand}>GINHAWA</span>
        <div className={styles.spacer} />
        {user !== null && (
          <span className={styles.user}>{user.full_name || user.username}</span>
        )}
        <button
          type="button"
          className={styles.logoutBtn}
          onClick={onLogout}
          disabled={loggingOut}
        >
          {loggingOut ? "Logging out…" : "Log out"}
        </button>
      </header>
      <main className={styles.main}>
        <Outlet />
      </main>
    </div>
  );
}
