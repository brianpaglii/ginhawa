import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, NetworkError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import styles from "./LoginPage.module.css";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(username, password);
      navigate("/dashboard", { replace: true });
    } catch (err) {
      setError(messageFor(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className={styles.page}>
      <form className={styles.card} onSubmit={onSubmit} noValidate>
        <div className={styles.brand}>
          <span className={styles.brandMark} aria-hidden>
            G
          </span>
          <h1 className={styles.title}>GINHAWA</h1>
        </div>
        <p className={styles.subtitle}>BHW portal sign-in</p>

        {error !== null && (
          <div role="alert" className={styles.error}>
            {error}
          </div>
        )}

        <div className={styles.field}>
          <label className={styles.label} htmlFor="login-username">
            Username
          </label>
          <input
            id="login-username"
            className={styles.input}
            name="username"
            autoComplete="username"
            required
            value={username}
            onChange={(e) => setUsername(e.currentTarget.value)}
            disabled={submitting}
          />
        </div>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="login-password">
            Password
          </label>
          <input
            id="login-password"
            className={styles.input}
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.currentTarget.value)}
            disabled={submitting}
          />
        </div>

        <button type="submit" className={styles.submit} disabled={submitting}>
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}

function messageFor(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Incorrect username or password.";
    return err.message || `Request failed (${err.status}).`;
  }
  if (err instanceof NetworkError) {
    return "Could not reach the server. Check your connection.";
  }
  return "Something went wrong. Please try again.";
}
