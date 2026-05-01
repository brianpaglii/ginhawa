# ADR 0005: Application is the sole writer of audit_log

- **Status:** Accepted
- **Date:** 2026-04-22
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira

## Context

The Data Privacy Act (RA 10173) and NPC Circular 2023-06 require that
data controllers handling sensitive personal information maintain an
audit trail of access and modification. GINHAWA implements this via an
`audit_log` table populated alongside every mutation and sensitive
read. The original schema design used PostgreSQL triggers to write
audit rows automatically on INSERT/UPDATE/DELETE of the patient-data
tables (citizens, sessions, measurements). The application would also
have written audit rows for sensitive reads (login, list-citizens,
export).

During Phase 1 implementation it became clear that this dual-writer
arrangement produced duplicate rows for every mutation: one from the
trigger, one from the application — both with `actor_type='system'`
because the trigger has no visibility into the request context.

## Decision

The application is the sole writer of `audit_log` for both mutations
and sensitive reads, via a single helper `services.audit.record_audit`.
Database-level triggers on the patient-data tables are removed.
Append-only enforcement on `audit_log` itself is retained via the
`audit_log_no_update` and `audit_log_no_delete` triggers and by
revoking UPDATE and DELETE on `audit_log` from the application's
database role.

## Alternatives considered

- _Triggers as sole writer:_ rejected because triggers cannot capture
  rich actor context (which BHW, which IP, which session, what
  reasoning) — they only see the row being changed. The DPA value of
  an audit log comes from its richness, not its existence.
- _Triggers as failsafe alongside application writes:_ rejected. The
  deduplication logic required to prevent double-writes is fragile and
  adds more complexity than it removes.
- _Application as sole writer with application-only enforcement:_
  rejected because revoked UPDATE/DELETE grants on the database role
  give us defense in depth at near-zero cost.

## Consequences

- Every mutation handler and every sensitive-read handler must call
  `record_audit` in the same transaction as its main operation. This
  is a discipline-and-code-review responsibility, not a database
  guarantee. CLAUDE.md and the code-review checklist enforce it.
- Audit rows are richly attributed (real BHW id, IP address, request
  details) instead of generic `'system'` for mutations.
- The append-only triggers on `audit_log` remain non-negotiable; they
  protect past entries from tampering even if the application is
  compromised.
