import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { useToast } from "../components/Toast";
import styles from "./AppLayout.module.css";

export function AppLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const [loggingOut, setLoggingOut] = useState(false);
  // Mobile-only: the sidebar is hidden by default at < 768px; the
  // hamburger button toggles it. Closing on every route change so a
  // tap on a nav link doesn't leave the panel obscuring the page.
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const isAdmin = user?.role === "admin";

  async function onLogout() {
    setLoggingOut(true);
    try {
      await logout();
      toast.success({ title: "Signed out" });
      navigate("/login", { replace: true });
    } catch {
      toast.error({
        title: "Sign-out failed",
        message: "We logged you out locally; you may want to retry.",
      });
      navigate("/login", { replace: true });
    } finally {
      setLoggingOut(false);
    }
  }

  // Collapse mobile nav whenever the URL changes — a tap on a nav
  // link shouldn't leave the panel obscuring the destination.
  useEffect(() => {
    setMobileNavOpen(false);
  }, [location.pathname]);

  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <button
          type="button"
          className={styles.menuBtn}
          aria-label="Toggle navigation"
          aria-expanded={mobileNavOpen}
          onClick={() => setMobileNavOpen((v) => !v)}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>
        <span className={styles.brand}>
          <span className={styles.brandMark} aria-hidden>
            G
          </span>
          GINHAWA
        </span>
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
      <div className={styles.body}>
        <nav
          className={styles.sidebar}
          aria-label="Primary"
          data-mobile-open={mobileNavOpen}
        >
          <ul className={styles.navList}>
            <li>
              <NavLink
                to="/sessions"
                className={({ isActive }) =>
                  isActive
                    ? `${styles.navLink} ${styles.navLinkActive}`
                    : styles.navLink
                }
              >
                Sessions
              </NavLink>
            </li>
            <li>
              <NavLink
                to="/citizens"
                className={({ isActive }) =>
                  isActive
                    ? `${styles.navLink} ${styles.navLinkActive}`
                    : styles.navLink
                }
              >
                Citizens
              </NavLink>
            </li>
            {isAdmin && (
              <li>
                <NavLink
                  to="/audit-log"
                  className={({ isActive }) =>
                    isActive
                      ? `${styles.navLink} ${styles.navLinkActive}`
                      : styles.navLink
                  }
                >
                  Audit log
                </NavLink>
              </li>
            )}
          </ul>
        </nav>
        <main className={styles.main}>
          <Outlet />
        </main>
      </div>
      <footer className={styles.footer}>GINHAWA Health Kiosk · v0.1</footer>
    </div>
  );
}
