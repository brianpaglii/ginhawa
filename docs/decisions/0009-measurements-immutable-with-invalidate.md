# ADR 0009: Measurements are immutable; corrections via invalidation

- **Status:** Accepted
- **Date:** 2026-04-22
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira

## Context

Measurements (BP readings, weight, height, etc.) sometimes need to be
"corrected" — for example, when a sensor was miscalibrated or the
wrong cuff size was used. The naive approach is a PATCH endpoint that
overwrites the value. This destroys evidence of the original reading,
which is incompatible with both clinical record-keeping practice and
the DPA's audit-trail requirement.

## Decision

Measurements have **no PATCH endpoint for the value**. The value, unit,
type, and source_device are write-once. To "correct" a measurement,
the user calls `PATCH /api/v1/measurements/{id}/invalidate` with a
`reason` field. This sets `is_valid = 0` and stores the reason in
`validation_notes`. Subsequent measurements are entered as new rows.

Default list and report queries filter `is_valid = 1`. Audit-log views
include invalid measurements with their reasons.

## Alternatives considered

- _Allow PATCH on value:_ rejected as described above.
- _Hard-delete invalidated measurements:_ rejected because the
  evidence-preservation argument applies symmetrically to deletion.
- _Soft-delete via a separate `deleted_at` timestamp:_ rejected in
  favor of `is_valid` because invalidation is a richer concept than
  deletion — the row is preserved precisely _because_ the original
  capture happened, not in spite of it.

## Consequences

- Measurement history is append-only at the row level. The "correct"
  version of a measurement is whichever valid row has the latest
  `measured_at` for that type within a session.
- BHW UI must distinguish valid from invalid measurements when
  displaying a citizen's history.
- The kiosk cannot retroactively correct a measurement once it has
  synced. If a kiosk-side validation is later found to be wrong, the
  invalidation flow runs from the BHW portal.
