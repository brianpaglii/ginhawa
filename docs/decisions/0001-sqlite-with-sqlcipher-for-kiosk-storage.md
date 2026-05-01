# ADR 0001: SQLite with SQLCipher for kiosk local storage

- **Status:** Accepted
- **Date:** 04-20-2026
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira

## Context

The GINHAWA kiosk is offline-first: it must operate correctly when the
barangay's internet connection is slow, intermittent, or absent. This
requires a local data store on the Raspberry Pi 5 capable of holding
citizen records, sessions, measurements, and an audit trail until the
kiosk can sync to the cloud.

The Data Privacy Act (RA 10173) and NPC Circular 2023-06 require
"appropriate organizational and technical measures" to protect personal
information. For a kiosk that may be deployed in a barangay health
center with limited physical security, the local database must be
encrypted at rest — a stolen SD card should not yield readable patient
data.

## Decision

Use **SQLite as the local database engine, with SQLCipher for
transparent at-rest encryption**. The encryption key is stored
separately from the database file, on the Pi's filesystem with
restrictive permissions (or in a system keyring where available).

## Alternatives considered

- _PostgreSQL on the Pi:_ rejected as overkill for a single-user kiosk.
  Adds operational complexity (service management, backup tooling) without
  benefit at the kiosk's scale.
- _Plain SQLite without encryption:_ rejected as non-compliant with the
  DPA's at-rest protection requirement for sensitive personal information.
- _DuckDB with manual file-level encryption:_ rejected; the integrated
  encryption story of SQLCipher is more auditable than rolling our own.

## Consequences

- The data-access layer must issue `PRAGMA key = '<key>'` immediately
  after every new connection. Failure to do so leaves the database
  effectively unencrypted.
- Key management is now an operational concern: provisioning, rotation,
  and recovery procedures must exist and be documented in the deployment
  runbook.
- SQLite's single-writer constraint means the kiosk's data layer must
  serialize writes; this is acceptable for a single-user kiosk but
  worth noting for any future multi-process design.
- The cloud uses PostgreSQL, so schema definitions must work for both;
  `schema.sql` documents the canonical schema with type substitutions.
