# Changelog

All notable changes to the GINHAWA project are recorded here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses semantic versioning for the database schema.

## [Unreleased]

### Added

- **Schema v1.0.0** — initial authoritative database schema at `schema.sql`.
  Defines the core patient-data hierarchy (`citizens` → `sessions` →
  `measurements`), the append-only `audit_log` with enforcement triggers,
  per-kiosk `device_config` key-value store, cloud-only BHW portal `users`,
  audit triggers on patient-data mutations, retention-task reference SQL,
  and the `schema_version` row pinned at `1.0.0`. Both `kiosk/alembic/`
  and `cloud/alembic/` mirror this schema with dialect-specific type
  substitutions; future changes must land in this file together with
  matching migrations on both sides.
