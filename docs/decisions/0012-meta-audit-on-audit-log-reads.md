# ADR 0012: Reading the audit log writes a meta-audit row

- **Status:** Accepted
- **Date:** 2026-04-22
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira

## Context

The audit log is the system's accountability mechanism. Per DPA
accountability principles and NPC Circular 2023-06's audit-trail
guidance, _who reads the audit log itself_ is part of the trail. An
admin investigating an incident should leave a record of having done
so, with the filter criteria applied.

## Decision

The `GET /api/v1/audit-log` handler calls `record_audit` after
fetching the result set, with `action='read_audit_log'`,
`actor_type=current_user.role`, `actor_id=current_user.id`, and
`details` containing the filter parameters and the total count
returned. This row is itself an audit_log entry, visible to subsequent
reads.

## Alternatives considered

- _Skip meta-audit:_ rejected. Without it, an admin can read the audit
  log silently, undermining the accountability invariant.
- _Suppress meta-audit rows from their own list:_ rejected. Excluding
  any rows from the audit log breaks its append-only-and-complete
  guarantee. Admins navigating the log will see meta-audits; this is
  acceptable and correct.

## Consequences

- The audit log grows by one row per read. At expected admin usage
  (a few reads per day), this is negligible.
- Pagination over an unfiltered audit-log query has a known one-row
  shift between consecutive `?offset=N` calls because each call
  appends a row to the top of the DESC-ordered set. The Phase 4 portal
  view will use keyset pagination to avoid this; the API contract is
  unchanged. ADR-0013 documents this.
