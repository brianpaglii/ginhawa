import { Component, type ErrorInfo, type ReactNode } from "react";

import styles from "./ErrorBoundary.module.css";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
  componentStack: string | null;
}

// React's error-boundary contract still requires class components.
// We catch render-time errors anywhere in the protected-routes
// subtree and present a friendly fallback that lets the user retry
// without reloading their entire session. Technical detail goes
// behind a <details> so it doesn't intimidate non-technical users.
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, componentStack: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // No remote logging service yet; console.error is the agreed
    // local trace surface for v1.
    // eslint-disable-next-line no-console
    console.error(
      "ErrorBoundary caught a render error:",
      error,
      info.componentStack,
    );
    this.setState({ componentStack: info.componentStack ?? null });
  }

  reset = () => {
    this.setState({ error: null, componentStack: null });
  };

  reload = () => {
    window.location.reload();
  };

  render(): ReactNode {
    if (this.state.error === null) {
      return this.props.children;
    }
    return (
      <div className={styles.shell}>
        <div role="alert" className={styles.card}>
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
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
          <h1 className={styles.title}>Something went wrong</h1>
          <p className={styles.message}>
            The page hit an unexpected error. You can try the action again or
            reload the portal.
          </p>
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.primary}
              onClick={this.reload}
            >
              Reload
            </button>
            <button
              type="button"
              className={styles.secondary}
              onClick={this.reset}
            >
              Try again
            </button>
          </div>
          <details className={styles.details}>
            <summary className={styles.summary}>Technical details</summary>
            <pre className={styles.stack}>
              {this.state.error.message}
              {this.state.componentStack ?? ""}
            </pre>
          </details>
        </div>
      </div>
    );
  }
}
