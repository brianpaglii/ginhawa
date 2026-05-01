# ADR 0015: Soft references for created_by / revoked_by columns

- **Status:** Accepted
- **Date:** 04-30-2026
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira

## Context

Tables that record human action need to reference users:
`device_credentials.created_by`, `device_credentials.revoked_by`, and
`audit_log.actor_id` for non-system actors. The naive choice is a
FOREIGN KEY constraint pointing at `users.id`. Two operational
realities make this problematic:

1. The DPA's right-to-erasure (§16(e)) requires that user records can
   be deleted on request. A `FOREIGN KEY ... ON DELETE RESTRICT`
   constraint would block such deletions.
2. Erasing the audit trail along with the user (`ON DELETE CASCADE`)
   would destroy exactly the historical accountability the audit log
   exists to preserve.

## Decision

Reference columns linking to users are **soft references** — TEXT or
UUID columns with no FOREIGN KEY constraint. Application-level joins
resolve them when displaying audit data; missing-user cases are
handled by the application as "user no longer exists."

This applies to:

- `device_credentials.created_by`
- `device_credentials.revoked_by`
- `audit_log.actor_id` (already implicit; this ADR formalizes the
  pattern for future tables)
- Any future table with a human-action reference column

## Alternatives considered

- _FK with `ON DELETE RESTRICT`:_ rejected. Blocks right-to-erasure
  under DPA §16(e).
- _FK with `ON DELETE CASCADE`:_ rejected. Erases audit history along
  with the user, defeating the audit log's purpose.
- _FK with `ON DELETE SET NULL`:_ rejected. Loses the actor_id, so
  reports cannot tell "this credential was created by a user who has
  since been erased" from "this credential has no recorded creator."
  We want the former, because audit trails should preserve the
  identifier even if the user record is gone.

## Consequences

- Referential integrity for these columns is application
  responsibility, not database-enforced. A typo in `created_by` would
  not be caught at insert time.
- Reports and audit views must handle the missing-user case gracefully:
  display "user 12345 (no longer in system)" rather than crashing on
  a join failure.
- The audit trail survives user erasure, which is the desired property
  for DPA-compliant accountability.
- Hard-deletion of users is now a viable operation; the right-to-erasure
  workflow can complete cleanly without referential cascades.
- Tests must exercise the missing-user path explicitly: any view that
  joins to users must have a test case where the join target has been
  deleted.
