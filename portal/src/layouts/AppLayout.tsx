import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/use-auth";
import { useToast } from "../components/use-toast";
import styles from "./AppLayout.module.css";

export function AppLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const [loggingOut, setLoggingOut] = useState(false);
  // Mobile-only: the sidebar is hidden by default at < 768px; the
  // hamburger button toggles it. The panel closes on the link
  // click handlers below rather than via a route-change effect.
  // (Effect-driven setState got flagged by react-hooks/set-state-
  // in-effect because navigation isn't an external system — it's
  // already React state in disguise — so the closure is best
  // colocated with the user action that causes it.)
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

  function closeMobileNav() {
    setMobileNavOpen(false);
  }

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
            <svg viewBox="0 0 64 64" width="22" height="22">
              <circle cx="32" cy="32" r="30" fill="currentColor" />
              <rect
                x="27"
                y="14"
                width="10"
                height="36"
                rx="2"
                fill="#ffffff"
              />
              <rect
                x="14"
                y="27"
                width="36"
                height="10"
                rx="2"
                fill="#ffffff"
              />
            </svg>
          </span>
          <span className={styles.brandText}>
            <span className={styles.brandTitle}>GINHAWA</span>
            <span className={styles.brandSubtitle}>Health Kiosk Portal</span>
          </span>
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
                to="/dashboard"
                onClick={closeMobileNav}
                className={({ isActive }) =>
                  isActive
                    ? `${styles.navLink} ${styles.navLinkActive}`
                    : styles.navLink
                }
              >
                Dashboard
              </NavLink>
            </li>
            <li>
              <NavLink
                to="/sessions"
                onClick={closeMobileNav}
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
                onClick={closeMobileNav}
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
                  onClick={closeMobileNav}
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
      <footer className={styles.footer}>
        GINHAWA · Barangay Health Worker Portal · v0.1.0
      </footer>
    </div>
  );
}
