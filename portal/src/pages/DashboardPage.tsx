import { useAuth } from "../auth/AuthContext";

export function DashboardPage() {
  const { user } = useAuth();
  const displayName = user ? user.full_name || user.username : "";
  return (
    <section>
      <h1>Hello, {displayName}.</h1>
      <p>
        This is a placeholder dashboard. Session list, citizen lookup, and audit
        log views land in the next prompts.
      </p>
    </section>
  );
}
