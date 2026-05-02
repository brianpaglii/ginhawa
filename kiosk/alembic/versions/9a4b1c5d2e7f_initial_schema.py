"""initial kiosk schema

Revision ID: 9a4b1c5d2e7f
Revises:
Create Date: 2026-05-02 00:00:00.000000

Mirrors the kiosk subset of /schema.sql. Tables created:
* citizens
* sessions
* measurements
* audit_log
* device_config

Cloud-only tables intentionally omitted: users, device_credentials.
The kiosk authenticates citizens by RFID and authenticates itself to
the cloud via the device API key in ``Settings.KIOSK_API_KEY`` — no
local user registry, no local credential registry.

The kiosk does NOT use the Postgres-style audit_log_no_update /
audit_log_no_delete triggers (ADR-0011 — Postgres-specific syntax).
Append-only on the kiosk is enforced by convention: the
``services.audit.record_audit`` helper is the only sanctioned writer.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9a4b1c5d2e7f"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# SQLite's CURRENT_TIMESTAMP returns "YYYY-MM-DD HH:MM:SS" without the
# "T" separator and without timezone — schema.sql uses datetime('now')
# which has the same shape. We keep the cloud-side ISO 8601 format
# discipline at the application layer; the column default here just
# guarantees a non-null value on direct INSERTs that omit it.
_DEFAULT_NOW = sa.text("datetime('now')")


def upgrade() -> None:
    op.create_table(
        "citizens",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("rfid_uid", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("dob", sa.String(), nullable=False),
        sa.Column("sex", sa.String(), nullable=False),
        sa.Column("barangay", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("consent_version", sa.String(), nullable=False),
        sa.Column(
            "consent_given_at",
            sa.String(),
            nullable=False,
            server_default=_DEFAULT_NOW,
        ),
        sa.Column(
            "registered_at",
            sa.String(),
            nullable=False,
            server_default=_DEFAULT_NOW,
        ),
        sa.Column("registered_by", sa.String(), nullable=True),
        sa.Column(
            "is_active", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("synced", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "updated_at",
            sa.String(),
            nullable=False,
            server_default=_DEFAULT_NOW,
        ),
        sa.CheckConstraint("sex IN ('M', 'F', 'O')", name="ck_citizens_sex"),
        sa.CheckConstraint("is_active IN (0, 1)", name="ck_citizens_is_active"),
        sa.CheckConstraint("synced IN (0, 1)", name="ck_citizens_synced"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_citizens_rfid", "citizens", ["rfid_uid"], unique=True)
    op.create_index("idx_citizens_barangay", "citizens", ["barangay"], unique=False)
    op.create_index(
        "idx_citizens_active_sync", "citizens", ["is_active", "synced"], unique=False
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("citizen_id", sa.String(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column(
            "started_at",
            sa.String(),
            nullable=False,
            server_default=_DEFAULT_NOW,
        ),
        sa.Column("ended_at", sa.String(), nullable=True),
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            server_default=sa.text("'in_progress'"),
        ),
        sa.Column("error_reason", sa.String(), nullable=True),
        sa.Column("measurement_path", sa.String(), nullable=True),
        sa.Column(
            "printed_status",
            sa.String(),
            nullable=False,
            server_default=sa.text("'not_requested'"),
        ),
        sa.Column("synced", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "updated_at",
            sa.String(),
            nullable=False,
            server_default=_DEFAULT_NOW,
        ),
        sa.CheckConstraint(
            "status IN ('in_progress', 'completed', 'aborted', 'error')",
            name="ck_sessions_status",
        ),
        sa.CheckConstraint(
            "measurement_path IN ('vitals', 'anthropometric', 'full')",
            name="ck_sessions_measurement_path",
        ),
        sa.CheckConstraint(
            "printed_status IN ('not_requested', 'printed_ok', "
            "'paper_out_pre', 'paper_out_mid', 'print_failed')",
            name="ck_sessions_printed_status",
        ),
        sa.CheckConstraint("synced IN (0, 1)", name="ck_sessions_synced"),
        sa.ForeignKeyConstraint(["citizen_id"], ["citizens.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_sessions_citizen", "sessions", ["citizen_id", "started_at"], unique=False
    )
    op.create_index(
        "idx_sessions_sync", "sessions", ["synced", "ended_at"], unique=False
    )
    op.create_index("idx_sessions_status", "sessions", ["status"], unique=False)

    op.create_table(
        "measurements",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(), nullable=False),
        sa.Column("source_device", sa.String(), nullable=False),
        sa.Column(
            "measured_at",
            sa.String(),
            nullable=False,
            server_default=_DEFAULT_NOW,
        ),
        sa.Column(
            "is_valid", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("validation_notes", sa.String(), nullable=True),
        sa.Column("raw_json", sa.String(), nullable=True),
        sa.Column("synced", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "updated_at",
            sa.String(),
            nullable=False,
            server_default=_DEFAULT_NOW,
        ),
        sa.CheckConstraint(
            "type IN ('systolic_bp', 'diastolic_bp', 'spo2', 'heart_rate', "
            "'temperature', 'height', 'weight', 'bmi')",
            name="ck_measurements_type",
        ),
        sa.CheckConstraint("is_valid IN (0, 1)", name="ck_measurements_is_valid"),
        sa.CheckConstraint("synced IN (0, 1)", name="ck_measurements_synced"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_meas_session", "measurements", ["session_id"], unique=False)
    op.create_index(
        "idx_meas_type_time", "measurements", ["type", "measured_at"], unique=False
    )
    op.create_index("idx_meas_sync", "measurements", ["synced"], unique=False)
    op.create_index("idx_meas_valid", "measurements", ["is_valid"], unique=False)

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column(
            "timestamp",
            sa.String(),
            nullable=False,
            server_default=_DEFAULT_NOW,
        ),
        sa.Column("actor_type", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("object_type", sa.String(), nullable=True),
        sa.Column("object_id", sa.String(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("details", sa.String(), nullable=True),
        sa.Column("synced", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint(
            "actor_type IN ('citizen', 'bhw', 'system', 'kiosk')",
            name="ck_audit_log_actor_type",
        ),
        sa.CheckConstraint("synced IN (0, 1)", name="ck_audit_log_synced"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_audit_time", "audit_log", ["timestamp"], unique=False)
    op.create_index(
        "idx_audit_actor", "audit_log", ["actor_type", "actor_id"], unique=False
    )
    op.create_index(
        "idx_audit_object", "audit_log", ["object_type", "object_id"], unique=False
    )

    op.create_table(
        "device_config",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column(
            "updated_at",
            sa.String(),
            nullable=False,
            server_default=_DEFAULT_NOW,
        ),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("device_config")
    op.drop_index("idx_audit_object", table_name="audit_log")
    op.drop_index("idx_audit_actor", table_name="audit_log")
    op.drop_index("idx_audit_time", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("idx_meas_valid", table_name="measurements")
    op.drop_index("idx_meas_sync", table_name="measurements")
    op.drop_index("idx_meas_type_time", table_name="measurements")
    op.drop_index("idx_meas_session", table_name="measurements")
    op.drop_table("measurements")
    op.drop_index("idx_sessions_status", table_name="sessions")
    op.drop_index("idx_sessions_sync", table_name="sessions")
    op.drop_index("idx_sessions_citizen", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("idx_citizens_active_sync", table_name="citizens")
    op.drop_index("idx_citizens_barangay", table_name="citizens")
    op.drop_index("idx_citizens_rfid", table_name="citizens")
    op.drop_table("citizens")
