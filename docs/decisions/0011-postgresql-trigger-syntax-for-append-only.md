# ADR 0011: Append-only enforcement on audit_log uses PostgreSQL exception, not SQLite ABORT

- **Status:** Accepted
- **Date:** 2026-04-22
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira

## Context

The schema definition originally used SQLite syntax for the
append-only triggers on `audit_log`:

    CREATE TRIGGER audit_log_no_update
    BEFORE UPDATE ON audit_log
    BEGIN
        SELECT RAISE(ABORT, 'audit_log is append-only');
    END;

`RAISE(ABORT, ...)` is SQLite-specific. The cloud Postgres database
needs equivalent enforcement.

## Decision

The cloud Alembic migration translates the SQLite triggers into
PL/pgSQL functions that raise an exception on UPDATE or DELETE attempts:

    CREATE OR REPLACE FUNCTION audit_log_no_update_fn()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'audit_log is append-only';
    END;
    $$ LANGUAGE plpgsql;

    CREATE TRIGGER audit_log_no_update
    BEFORE UPDATE ON audit_log
    FOR EACH ROW
    EXECUTE FUNCTION audit_log_no_update_fn();

The kiosk SQLite uses the original SQLite syntax. Both databases
behave identically from the application's perspective: an UPDATE or
DELETE attempt on audit_log raises an exception that surfaces as a
500-class error.

In addition to the triggers, UPDATE and DELETE on `audit_log` are
revoked from the application's database role on Postgres. This is
defense in depth: a future migration that accidentally drops the
trigger would still be blocked at the role level.

## Alternatives considered

- _Application-only enforcement (no triggers):_ rejected. ADR-0005
  notes that revoked grants are a low-cost defense-in-depth layer
  worth keeping.
- _Single trigger language across both databases:_ not technically
  possible. The trade-off is the small duplication in migration files.

## Consequences

- The SQLite and PostgreSQL migrations diverge on this one trigger
  pair. Both are tested.
- Future schema changes affecting `audit_log` must update both
  migration trees.
- The existing tests (`test_audit_log_update_rejected`,
  `test_audit_log_delete_rejected`) verify the behaviour against the
  test database; they do not need to be aware of the underlying SQL
  dialect.
