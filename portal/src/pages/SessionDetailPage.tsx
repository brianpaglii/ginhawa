import { Link, useParams } from "react-router-dom";

export function SessionDetailPage() {
  const { id } = useParams<{ id: string }>();
  return (
    <section>
      <h1>Session {id}</h1>
      <p>Detail view, charts, and measurement table land in a later prompt.</p>
      <p>
        <Link to="/sessions">← Back to sessions</Link>
      </p>
    </section>
  );
}
