# ADR 0013: Defer keyset pagination on audit_log to Phase 4

- **Status:** Accepted
- **Date:** 2026-04-22
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira

## Context

`GET /api/v1/audit-log` uses OFFSET/LIMIT pagination ordered by
`(timestamp DESC, id DESC)`. Combined with the meta-audit behaviour
described in ADR-0012, consecutive unfiltered paginated reads from the
same admin exhibit a one-row shift: each read appends a row that
becomes the new top of the DESC order, shifting the next page's
contents by one position.

The behaviour is correct (each query is a snapshot at execution time),
but it makes naive "next page" navigation in a UI subtly confusing.

## Decision

For Phase 1, accept the shift as a known property. Document it in this
ADR. Do not modify the API contract.

For Phase 4, the BHW portal's audit-log view will implement keyset
pagination using `(timestamp, id)` as the cursor:

    SELECT * FROM audit_log
    WHERE (timestamp, id) < (?, ?)
    ORDER BY timestamp DESC, id DESC
    LIMIT ?

This is invariant under appends and produces consistent next-page
behaviour without changing the underlying query semantics.

## Alternatives considered

- _Implement keyset pagination in Phase 1:_ rejected as
  overengineering for current scale. The shift only matters in the
  unfiltered case, which is not a realistic admin workflow.
- _Snapshot-based pagination using a server-side cursor or transaction
  isolation:_ rejected as significantly more complex and unnecessary.

## Consequences

- Filtered audit-log queries (the dominant case in real usage) are
  unaffected by the shift.
- Unfiltered next-page reads in the same admin session may show a
  one-row overlap. Acceptable for development; not exposed to a UI yet.
- A GitHub issue tagged `phase-4` tracks the keyset migration.
- The Phase 4 portal spec will reference this ADR.
