# ADR 0016: Kiosk audit_log is local forensic-only; cloud audit is rebuilt at sync time

- **Status:** Accepted
- **Date:** 2026-05-02
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira

## Context

The kiosk maintains a local `audit_log` table inside its SQLCipher
database (mirroring the schema's audit-log shape, with ADR-0011's
"app-only writer" discipline since SQLite has no Postgres-style
trigger / role-permission enforcement).

The cloud also maintains an `audit_log` table. Today, the cloud's
sync endpoints (`POST /api/v1/sync/{citizens,sessions,measurements}`)
write one cloud-side audit row per upload, attributed to
`actor_type='kiosk'` with `actor_id=<device_credentials.device_id>`
(see ADRs 0005 and 0014).

Question: should the kiosk **also** upload its local `audit_log` rows
to the cloud as a separate stream, so the cloud holds an exact mirror
of the kiosk's forensic record?

## Decision

The kiosk's local `audit_log` is **forensic-only**. Rows there are
NOT uploaded to the cloud as a separate stream. The cloud's canonical
audit trail is rebuilt from the sync endpoints' attribution.

## Alternatives considered

- **Option 1 (chosen): no separate audit upload.**
  - Kiosk audit rows are written locally and stay there.
  - Cloud audit rows are written by the cloud's sync handlers when
    citizens/sessions/measurements arrive.
  - Forensic story: the kiosk's local audit is available if someone
    inspects the kiosk physically. The cloud's audit is the canonical
    cross-deployment view.

- **Option 2 (deferred): separate audit upload stream.**
  - Kiosk syncs `audit_log` rows to the cloud via a fourth sync
    endpoint (e.g., `POST /api/v1/sync/audit-log`).
  - Cloud accepts and stores them with their original
    `actor_type` / `actor_id`.
  - Trade-off: more resilient against kiosk-side tampering of the
    pre-sync window, but creates two audit rows for every event the
    cloud already attributes (one written by the cloud's sync
    handler, one uploaded from the kiosk) — duplicate volume,
    schema drift risk, and merge / dedupe logic on the cloud.

## Consequences

- A kiosk that is compromised between writing audit rows and syncing
  the underlying data CAN have its local forensic record tampered
  with. The cloud's audit row (written at sync time) survives.
- A divergence between local kiosk audit count and cloud kiosk audit
  count is itself a useful forensic signal: it indicates that the
  kiosk wrote audit events for actions whose underlying data never
  reached the cloud, or vice-versa.
- The kiosk's `audit_log.synced` column is unused in practice today
  (always 0). Phase 3 may revisit; until then, the column stays for
  schema parity with the cloud and to avoid a destructive migration.
- This decision is revisited in Phase 3 once the threat model fully
  covers offline kiosk compromise. Specifically: if the threat model
  treats kiosk-side tampering between write and sync as a credible
  attack, Option 2 becomes the right call.

## Implementation note

The behavioural contract is captured in
`kiosk/src/ginhawa_kiosk/services/audit.py`'s module docstring under
"KIOSK AUDIT vs CLOUD AUDIT". Any code change that introduces a
kiosk-side audit upload must update both this ADR and that comment.
