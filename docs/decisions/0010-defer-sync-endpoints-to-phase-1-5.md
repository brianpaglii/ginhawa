# ADR 0010: Defer kiosk sync endpoints to Phase 1.5

- **Status:** Accepted (Phase 1.5 landed 2026-05-01)
- **Date:** 2026-04-22
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira

## Context

The original Phase 1 plan included Prompt 9: kiosk sync endpoints
backed by a `device_credentials` table. During Phase 1 verification we
discovered the prompt had been skipped between auth (Prompt 7) and
audit-log/seed (Prompts 8 and 10). Neither the table nor the endpoints
exist as of end-of-Phase-1.

## Decision

Defer the sync feature to a Phase 1.5 milestone before Phase 2 begins.
The seed script ships without device-credential seeding. A follow-up
commit will add that seeding once `device_credentials` exists.

## Alternatives considered

- _Build sync as part of seed work:_ rejected. Mixing seeding (a
  development tool) with new feature work compounds review risk and
  increases the size of one commit beyond what's reviewable.
- _Skip sync entirely until Phase 2:_ rejected. The kiosk cannot
  integrate with a cloud that has no sync endpoints. Phase 2 would
  block at its first integration test.
- _Build a stub `device_credentials` table now:_ rejected. An orphaned
  schema migration with no consuming code is worse than no migration.

## Consequences

- Phase 2 (kiosk) start was gated on Phase 1.5 (sync) completion.
- The Pass 3 smoke test was run in two parts: most scenarios at end of
  Phase 1, the kiosk-sync-specific scenarios after Phase 1.5 landed
  (PS1-PS10, all PASS as of 2026-05-01).
- The seed script had a documented `[DEFERRED]` notice in its stdout
  output explaining the gap; updated when device-credential seeding
  landed.
- The estimated calendar impact was ~2-3 days for Phase 1.5; actual
  was on schedule.
