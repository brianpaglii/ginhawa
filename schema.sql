-- =============================================================================
-- GINHAWA — AUTHORITATIVE DATABASE SCHEMA
-- =============================================================================
-- (a) This file is the single source of truth for the GINHAWA Health
--     Monitoring Kiosk database structure. All tables, indexes, triggers,
--     and constraints used by the system originate here.
--
-- (b) Both the kiosk (SQLite via SQLCipher) and the cloud backend
--     (PostgreSQL 16) mirror this schema. Dialect-specific type
--     substitutions (TEXT->VARCHAR, REAL->DOUBLE PRECISION,
--     AUTOINCREMENT->IDENTITY, etc.) are handled by Alembic migrations,
--     not by editing this file with conditional dialect syntax.
--
-- (c) ANY change to this file MUST be accompanied, in the same commit,
--     by matching Alembic migrations in BOTH `kiosk/alembic/` and
--     `cloud/alembic/`. The schema must not drift between modules:
--     a column added here that is not migrated on both sides is a bug.
--     Destructive changes (DROP COLUMN/TABLE, type changes on
--     data-bearing tables) require explicit human review before commit.
-- =============================================================================

-- ginhawa-schema.sql
-- =============================================================================
-- Authoritative database schema for the GINHAWA Health Monitoring Kiosk system.
--
-- This file is the SOURCE OF TRUTH for the database structure. Both the kiosk
-- (SQLite via SQLCipher) and the cloud backend (PostgreSQL) mirror this schema
-- with type substitutions handled by Alembic migrations:
--
--     SQLite          ->   PostgreSQL
--     ------------         -----------------
--     TEXT             ->  VARCHAR or TEXT
--     REAL             ->  DOUBLE PRECISION
--     INTEGER          ->  INTEGER
--     AUTOINCREMENT    ->  SERIAL or IDENTITY
--
-- Changes to this file MUST be accompanied by matching Alembic migrations in
-- BOTH `kiosk/alembic/` and `cloud/alembic/`. Do not allow the schema to drift
-- between modules.
--
-- Schema version: 1.0.0
-- Last updated:   2026-04-22
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Enable foreign-key enforcement.
-- SQLite disables foreign keys by default; the kiosk's data-access layer
-- must issue this PRAGMA on every new connection.
-- -----------------------------------------------------------------------------
PRAGMA foreign_keys = ON;


-- =============================================================================
-- CORE TABLES
-- =============================================================================
-- The core tables form a one-to-many hierarchy:
--     citizens 1 -- N sessions 1 -- N measurements
-- A citizen has many sessions; a session has many measurements.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- citizens: primary registry of kiosk users
-- -----------------------------------------------------------------------------
-- One row per registered citizen. Keyed by a system-generated UUID and linked
-- uniquely to an RFID card. The RFID UID is the natural key from the card
-- but is NOT the primary key, because card replacement (lost or damaged card)
-- must be supportable without rewriting every foreign key reference.
--
-- consent_version and consent_given_at record the version of the privacy
-- notice the citizen accepted at registration. Per NPC Circular 2023-04, if
-- the privacy notice is materially revised, existing citizens must be
-- re-prompted at their next session.
--
-- is_active = 0 represents soft-delete (right to erasure under DPA Section
-- 16(e)). Soft-deleted citizens are hidden from all portal views but remain
-- in the database for audit purposes until the retention period expires.
-- -----------------------------------------------------------------------------
CREATE TABLE citizens (
    id                  TEXT PRIMARY KEY,
    rfid_uid            TEXT NOT NULL UNIQUE,
    full_name           TEXT NOT NULL,
    dob                 TEXT NOT NULL,                  -- ISO 8601 date
    sex                 TEXT NOT NULL CHECK (sex IN ('M', 'F', 'O')),
    barangay            TEXT NOT NULL,
    phone               TEXT,                           -- optional, for future SMS features
    consent_version     TEXT NOT NULL,
    consent_given_at    TEXT NOT NULL DEFAULT (datetime('now')),
    registered_at       TEXT NOT NULL DEFAULT (datetime('now')),
    registered_by       TEXT,                           -- BHW user id, NULL for self-service
    is_active           INTEGER NOT NULL DEFAULT 1
                        CHECK (is_active IN (0, 1)),
    synced              INTEGER NOT NULL DEFAULT 0
                        CHECK (synced IN (0, 1)),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX idx_citizens_rfid ON citizens(rfid_uid);
CREATE INDEX idx_citizens_barangay ON citizens(barangay);
CREATE INDEX idx_citizens_active_sync ON citizens(is_active, synced);


-- -----------------------------------------------------------------------------
-- sessions: one row per kiosk visit
-- -----------------------------------------------------------------------------
-- A session represents the complete user-facing transaction from RFID tap
-- through the REPORT screen.
--
-- status values:
--   'in_progress' - session active; user is mid-flow
--   'completed'   - normal completion (REPORT shown, END reached)
--   'aborted'     - user walked away or cancelled before completion
--   'error'       - terminated due to system error; see error_reason
--
-- printed_status values track the receipt outcome separately from session
-- success, because a session is valid even if the printer fails:
--   'not_requested'  - user chose Finish Without Printing
--   'printed_ok'     - print completed successfully
--   'paper_out_pre'  - paper was absent when REPORT entered; option not offered
--   'paper_out_mid'  - paper exhausted during the print
--   'print_failed'   - other print error (cable, power, firmware, etc.)
--
-- measurement_path records which measurement subset the user selected at
-- the MENU screen, supporting reporting on usage patterns.
--
-- ON DELETE RESTRICT on citizen_id ensures we cannot hard-delete a citizen
-- who has any sessions; the soft-delete pathway (is_active = 0) is the only
-- way to remove an active citizen.
-- -----------------------------------------------------------------------------
CREATE TABLE sessions (
    id                  TEXT PRIMARY KEY,
    citizen_id          TEXT NOT NULL REFERENCES citizens(id) ON DELETE RESTRICT,
    device_id           TEXT NOT NULL,
    started_at          TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at            TEXT,
    status              TEXT NOT NULL DEFAULT 'in_progress'
                        CHECK (status IN (
                            'in_progress',
                            'completed',
                            'aborted',
                            'error'
                        )),
    error_reason        TEXT,
    measurement_path    TEXT
                        CHECK (measurement_path IN (
                            'vitals',
                            'anthropometric',
                            'full'
                        )),
    printed_status      TEXT NOT NULL DEFAULT 'not_requested'
                        CHECK (printed_status IN (
                            'not_requested',
                            'printed_ok',
                            'paper_out_pre',
                            'paper_out_mid',
                            'print_failed'
                        )),
    synced              INTEGER NOT NULL DEFAULT 0
                        CHECK (synced IN (0, 1))
);

CREATE INDEX idx_sessions_citizen ON sessions(citizen_id, started_at);
CREATE INDEX idx_sessions_sync ON sessions(synced, ended_at);
CREATE INDEX idx_sessions_status ON sessions(status);


-- -----------------------------------------------------------------------------
-- measurements: one row per vital sign captured during a session
-- -----------------------------------------------------------------------------
-- The schema is deliberately sensor-agnostic: rather than a separate column
-- for each parameter (systolic, diastolic, spo2, height, weight, temperature),
-- the table stores a typed measurement per row. This mirrors the structure
-- used by FHIR Observations and makes adding new measurement types in the
-- future a data-model change rather than a schema change.
--
-- value is REAL (not TEXT). Numeric storage is non-negotiable for community
-- health screening: BHWs need to query for ranges ("all systolic readings
-- above 140 mmHg"), aggregate across barangays, and analyze trends.
--
-- type values map to the GINHAWA scope:
--   'systolic_bp', 'diastolic_bp' - from Omron HEM-7155T (BLE)
--   'spo2', 'heart_rate'          - from MAX30100 (ESP32-A via MQTT)
--   'temperature'                 - from MLX90640BAB (ESP32-B via MQTT)
--   'height'                      - from VL53L0X (ESP32-B via MQTT)
--   'weight'                      - from Xiaomi scale (BLE)
--   'bmi'                         - derived; computed by the Pi
--
-- source_device records the origin device (e.g. 'omron_hem7155t',
-- 'esp32_a', 'xiaomi_s200', 'derived', 'manual:bhw'). 'manual:bhw' is the
-- fallback path when BLE parsing fails and a BHW enters a value from the
-- device's display.
--
-- is_valid = 1 means the reading passed physiological-range validation;
-- is_valid = 0 means the reading is preserved for diagnostic auditability
-- but excluded from clinical reports. validation_notes captures the reason.
--
-- raw_json holds the verbose sensor payload for diagnostic replay; it is
-- nulled by the retention task 30 days after successful cloud sync to limit
-- disk growth (see RETENTION TASKS at the bottom of this file).
--
-- ON DELETE CASCADE on session_id means deleting a session removes its
-- measurements; this is consistent with the right-to-erasure pathway, where
-- hard-deletion of a citizen CASCADEs through sessions to measurements.
-- -----------------------------------------------------------------------------
CREATE TABLE measurements (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    type                TEXT NOT NULL CHECK (type IN (
                            'systolic_bp', 'diastolic_bp',
                            'spo2', 'heart_rate',
                            'temperature',
                            'height', 'weight',
                            'bmi'
                        )),
    value               REAL NOT NULL,
    unit                TEXT NOT NULL,
    source_device       TEXT NOT NULL,
    measured_at         TEXT NOT NULL DEFAULT (datetime('now')),
    is_valid            INTEGER NOT NULL DEFAULT 1
                        CHECK (is_valid IN (0, 1)),
    validation_notes    TEXT,
    raw_json            TEXT,
    synced              INTEGER NOT NULL DEFAULT 0
                        CHECK (synced IN (0, 1))
);

CREATE INDEX idx_meas_session ON measurements(session_id);
CREATE INDEX idx_meas_type_time ON measurements(type, measured_at);
CREATE INDEX idx_meas_sync ON measurements(synced);
CREATE INDEX idx_meas_valid ON measurements(is_valid);


-- =============================================================================
-- SUPPORTING TABLES
-- =============================================================================

-- -----------------------------------------------------------------------------
-- audit_log: append-only record of all access to and modification of data
-- -----------------------------------------------------------------------------
-- Required by the Data Privacy Act of 2012 (RA 10173) and NPC Circular
-- 2023-06 (Security of Personal Data). Every read of a citizen record,
-- every modification, every export, and every login writes one row here.
--
-- The table is enforced as append-only by triggers (see below) that raise
-- ABORT on UPDATE or DELETE attempts. Direct database modification of
-- audit_log is forbidden; legitimate corrections are made by appending new
-- rows that document the correction.
--
-- actor_type values:
--   'citizen' - kiosk user authenticated by RFID
--   'bhw'     - Barangay Health Worker logged into the portal
--   'system'  - automated system action (sync, retention task, etc.)
--   'admin'   - administrator with elevated privileges
--   'kiosk'   - the kiosk itself, acting on behalf of an unattended
--               (self-service) registration where no BHW is present.
--               actor_id holds the device_credentials.device_id.
--
-- details is a JSON blob with operation-specific metadata: for a 'login'
-- event, the user agent; for an 'export' event, the filter criteria
-- applied; for an 'update' event, a diff of changed fields.
-- -----------------------------------------------------------------------------
CREATE TABLE audit_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp           TEXT NOT NULL DEFAULT (datetime('now')),
    actor_type          TEXT NOT NULL CHECK (actor_type IN (
                            'citizen', 'bhw', 'system', 'admin', 'kiosk'
                        )),
    actor_id            TEXT,
    action              TEXT NOT NULL,
    object_type         TEXT,
    object_id           TEXT,
    ip_address          TEXT,
    details             TEXT,                           -- JSON
    synced              INTEGER NOT NULL DEFAULT 0
                        CHECK (synced IN (0, 1))
);

CREATE INDEX idx_audit_time ON audit_log(timestamp);
CREATE INDEX idx_audit_actor ON audit_log(actor_type, actor_id);
CREATE INDEX idx_audit_object ON audit_log(object_type, object_id);


-- -----------------------------------------------------------------------------
-- audit_log append-only enforcement
-- Any UPDATE or DELETE on audit_log is rejected. Application code can
-- only INSERT.
-- -----------------------------------------------------------------------------
CREATE TRIGGER audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE TRIGGER audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;


-- -----------------------------------------------------------------------------
-- device_config: per-kiosk key-value settings
-- -----------------------------------------------------------------------------
-- A simple key-value store separated from patient data so configuration
-- changes do not interact with the patient-data schema. Initial seed values
-- are written at kiosk commissioning time.
--
-- Expected keys:
--   'kiosk_id'              - this kiosk's unique UUID, generated at install
--   'deployment_barangay'   - default barangay for this kiosk
--   'vl53l0x_offset_mm'     - calibration offset for height sensor (mm)
--   'mlx90640_emissivity'   - emissivity setting for thermal sensor
--   'last_sync_at'          - ISO 8601 timestamp of last successful sync
--   'consent_version'       - current privacy notice version this kiosk shows
--   'language_default'      - 'en' or 'tl'
-- -----------------------------------------------------------------------------
CREATE TABLE device_config (
    key                 TEXT PRIMARY KEY,
    value               TEXT NOT NULL,
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Seeded at commissioning time (sample values; replace at install):
-- INSERT INTO device_config VALUES
--     ('kiosk_id', '<uuid>', datetime('now')),
--     ('deployment_barangay', '<name>', datetime('now')),
--     ('vl53l0x_offset_mm', '0', datetime('now')),
--     ('mlx90640_emissivity', '0.98', datetime('now')),
--     ('last_sync_at', '0', datetime('now')),
--     ('consent_version', '1.0', datetime('now')),
--     ('language_default', 'en', datetime('now'));


-- -----------------------------------------------------------------------------
-- users: BHW portal accounts (CLOUD ONLY; not replicated to kiosk)
-- -----------------------------------------------------------------------------
-- Administrative users of the Barangay Health Worker web portal. This table
-- exists in the cloud database only; the kiosk itself has no concept of a
-- BHW login because all on-kiosk operations are performed by RFID-
-- authenticated citizens.
--
-- password_hash uses argon2id (OWASP-recommended for new deployments);
-- plaintext passwords are never stored.
--
-- assigned_barangay restricts visibility: a BHW with a non-NULL
-- assigned_barangay can only view records from that barangay. Administrators
-- have assigned_barangay = NULL and can see all records (every access is
-- audit-logged).
--
-- This DDL is included in this single file for reference, but should be
-- applied only to the cloud Postgres database; the kiosk Alembic migration
-- skips this table.
-- -----------------------------------------------------------------------------
CREATE TABLE users (
    id                  TEXT PRIMARY KEY,
    username            TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,
    full_name           TEXT NOT NULL,
    role                TEXT NOT NULL CHECK (role IN (
                            'bhw', 'admin', 'data_viewer'
                        )),
    assigned_barangay   TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1
                        CHECK (is_active IN (0, 1)),
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    last_login_at       TEXT
);

CREATE UNIQUE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_role_barangay ON users(role, assigned_barangay);


-- -----------------------------------------------------------------------------
-- device_credentials: API-key credentials for kiosk-to-cloud sync (CLOUD ONLY)
-- -----------------------------------------------------------------------------
-- One row per kiosk that authenticates to the cloud sync endpoints. The
-- plaintext API key is shown to the admin once at creation time and never
-- stored — only the argon2id hash is persisted, exactly the same way user
-- passwords are handled. If a key is lost the credential must be revoked
-- and a new one created; there is no recovery path by design.
--
-- Revocation is the soft-delete pathway: revoked_at and revoked_by are set
-- and the row remains for audit. Reactivation is intentionally unsupported
-- — a revoked credential is dead, period; new kiosks get new credentials.
--
-- created_by / revoked_by hold user IDs but are NOT declared as foreign keys
-- to users(id). They are soft references; the application looks up actor
-- detail when rendering a credential's history. Decoupling lets us hard-
-- delete users (per DPA right-to-erasure) without orphaning credentials.
--
-- Like the users table, this DDL is cloud-only; the kiosk Alembic migration
-- skips it.
-- -----------------------------------------------------------------------------
CREATE TABLE device_credentials (
    device_id           TEXT PRIMARY KEY,
    api_key_hash        TEXT NOT NULL,
    description         TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    created_by          TEXT NOT NULL,
    revoked_at          TEXT,
    revoked_by          TEXT,
    last_seen_at        TEXT
);

CREATE UNIQUE INDEX idx_device_credentials_description
    ON device_credentials(description);
CREATE INDEX idx_device_credentials_revoked_at
    ON device_credentials(revoked_at);


-- =============================================================================
-- AUDIT WRITES — APPLICATION-LAYER ONLY
-- =============================================================================
-- Audit rows for INSERT/UPDATE on citizens, sessions, and measurements are
-- written by the application's data-access layer (see
-- `cloud/src/ginhawa_cloud/services/audit.py` / kiosk equivalent), NOT by
-- database triggers.
--
-- The two append-only triggers on audit_log itself (audit_log_no_update,
-- audit_log_no_delete, defined above with the audit_log table) remain — they
-- block UPDATE/DELETE on the audit table but do not write rows.
--
-- Earlier revisions of this file defined per-table mutation triggers; those
-- were removed because they double-wrote rows alongside the application
-- writer and could not see real actor identity (every row was actor_type =
-- 'system'). Letting the request handler write the audit row keeps the
-- actor accurate when authentication is wired up.
-- =============================================================================


-- =============================================================================
-- RETENTION TASKS
-- =============================================================================
-- These statements are not part of the schema definition itself but are
-- documented here for reference. They are executed by a scheduled task on
-- the Pi (kiosk) and on the cloud backend. The schedule is daily, typically
-- at 03:00 local time when the kiosk is idle.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Null out raw_json for measurements older than 30 days that have synced.
-- The numeric value and unit remain intact for longitudinal analysis;
-- only the verbose diagnostic payload is dropped.
-- -----------------------------------------------------------------------------
-- UPDATE measurements
--   SET raw_json = NULL
--   WHERE synced = 1
--     AND raw_json IS NOT NULL
--     AND measured_at < datetime('now', '-30 days');

-- -----------------------------------------------------------------------------
-- Hard-delete soft-deleted citizens after 5 years past the soft-delete date.
-- CASCADEs through sessions and measurements. Requires the data-access layer
-- to also write a final audit_log entry recording the hard-delete.
-- -----------------------------------------------------------------------------
-- DELETE FROM citizens
--   WHERE is_active = 0
--     AND updated_at < datetime('now', '-5 years');

-- -----------------------------------------------------------------------------
-- Reclaim disk space after substantial deletes.
-- -----------------------------------------------------------------------------
-- VACUUM;


-- =============================================================================
-- SCHEMA VERSION TABLE
-- =============================================================================
-- A single-row table recording the schema version. Updated by Alembic
-- migrations; humans should not write to this table directly.
-- =============================================================================
CREATE TABLE schema_version (
    version             TEXT PRIMARY KEY,
    applied_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO schema_version (version) VALUES ('1.0.0');


-- =============================================================================
-- END OF SCHEMA
-- =============================================================================
