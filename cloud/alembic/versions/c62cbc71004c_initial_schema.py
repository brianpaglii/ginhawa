"""initial schema

Revision ID: c62cbc71004c
Revises:
Create Date: 2026-04-28 15:15:54.798367

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c62cbc71004c"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Postgres equivalent of SQLite's `datetime('now')` rendered as RFC 3339 UTC.
# The kiosk stores timestamps as ISO 8601 strings; this default keeps the
# cloud columns producing the same shape when rows are inserted via raw SQL.
_ISO_UTC_NOW = """to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "timestamp",
            sa.String(),
            nullable=False,
            server_default=sa.text(_ISO_UTC_NOW),
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
            "actor_type IN ('citizen', 'bhw', 'system', 'admin')",
            name="ck_audit_log_actor_type",
        ),
        sa.CheckConstraint("synced IN (0, 1)", name="ck_audit_log_synced"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_audit_actor", "audit_log", ["actor_type", "actor_id"], unique=False
    )
    op.create_index(
        "idx_audit_object", "audit_log", ["object_type", "object_id"], unique=False
    )
    op.create_index("idx_audit_time", "audit_log", ["timestamp"], unique=False)
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
            server_default=sa.text(_ISO_UTC_NOW),
        ),
        sa.Column(
            "registered_at",
            sa.String(),
            nullable=False,
            server_default=sa.text(_ISO_UTC_NOW),
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
            server_default=sa.text(_ISO_UTC_NOW),
        ),
        sa.CheckConstraint("sex IN ('M', 'F', 'O')", name="ck_citizens_sex"),
        sa.CheckConstraint("is_active IN (0, 1)", name="ck_citizens_is_active"),
        sa.CheckConstraint("synced IN (0, 1)", name="ck_citizens_synced"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_citizens_active_sync", "citizens", ["is_active", "synced"], unique=False
    )
    op.create_index("idx_citizens_barangay", "citizens", ["barangay"], unique=False)
    op.create_index("idx_citizens_rfid", "citizens", ["rfid_uid"], unique=True)
    op.create_table(
        "device_config",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column(
            "updated_at",
            sa.String(),
            nullable=False,
            server_default=sa.text(_ISO_UTC_NOW),
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("assigned_barangay", sa.String(), nullable=True),
        sa.Column(
            "is_active", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "created_at",
            sa.String(),
            nullable=False,
            server_default=sa.text(_ISO_UTC_NOW),
        ),
        sa.Column("last_login_at", sa.String(), nullable=True),
        sa.CheckConstraint(
            "role IN ('bhw', 'admin', 'data_viewer')", name="ck_users_role"
        ),
        sa.CheckConstraint("is_active IN (0, 1)", name="ck_users_is_active"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_users_role_barangay",
        "users",
        ["role", "assigned_barangay"],
        unique=False,
    )
    op.create_index("idx_users_username", "users", ["username"], unique=True)
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("citizen_id", sa.String(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column(
            "started_at",
            sa.String(),
            nullable=False,
            server_default=sa.text(_ISO_UTC_NOW),
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
        sa.CheckConstraint(
            "measurement_path IN ('vitals', 'anthropometric', 'full')",
            name="ck_sessions_measurement_path",
        ),
        sa.CheckConstraint(
            "printed_status IN ('not_requested', 'printed_ok', 'paper_out_pre', 'paper_out_mid', 'print_failed')",
            name="ck_sessions_printed_status",
        ),
        sa.CheckConstraint(
            "status IN ('in_progress', 'completed', 'aborted', 'error')",
            name="ck_sessions_status",
        ),
        sa.CheckConstraint("synced IN (0, 1)", name="ck_sessions_synced"),
        sa.ForeignKeyConstraint(["citizen_id"], ["citizens.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_sessions_citizen", "sessions", ["citizen_id", "started_at"], unique=False
    )
    op.create_index("idx_sessions_status", "sessions", ["status"], unique=False)
    op.create_index(
        "idx_sessions_sync", "sessions", ["synced", "ended_at"], unique=False
    )
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
            server_default=sa.text(_ISO_UTC_NOW),
        ),
        sa.Column(
            "is_valid", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("validation_notes", sa.String(), nullable=True),
        sa.Column("raw_json", sa.String(), nullable=True),
        sa.Column("synced", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint(
            "type IN ('systolic_bp', 'diastolic_bp', 'spo2', 'heart_rate', 'temperature', 'height', 'weight', 'bmi')",
            name="ck_measurements_type",
        ),
        sa.CheckConstraint("is_valid IN (0, 1)", name="ck_measurements_is_valid"),
        sa.CheckConstraint("synced IN (0, 1)", name="ck_measurements_synced"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_meas_session", "measurements", ["session_id"], unique=False)
    op.create_index("idx_meas_sync", "measurements", ["synced"], unique=False)
    op.create_index(
        "idx_meas_type_time", "measurements", ["type", "measured_at"], unique=False
    )
    op.create_index("idx_meas_valid", "measurements", ["is_valid"], unique=False)

    op.create_table(
        "schema_version",
        sa.Column("version", sa.String(), nullable=False),
        sa.Column(
            "applied_at",
            sa.String(),
            nullable=False,
            server_default=sa.text(_ISO_UTC_NOW),
        ),
        sa.PrimaryKeyConstraint("version"),
    )
    op.execute("INSERT INTO schema_version (version) VALUES ('1.0.0')")

    # -------------------------------------------------------------------------
    # audit_log append-only enforcement (SQLite RAISE(ABORT,...) -> Postgres
    # RAISE EXCEPTION). Both UPDATE and DELETE share the same trigger function.
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE FUNCTION audit_log_no_modify() RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_log_no_update
        BEFORE UPDATE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_no_modify();
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_log_no_delete
        BEFORE DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_no_modify();
        """
    )

    # -------------------------------------------------------------------------
    # citizens audit triggers (insert / non-soft-delete update / soft-delete)
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE FUNCTION audit_citizens_insert_fn() RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO audit_log (
                timestamp, actor_type, actor_id, action,
                object_type, object_id, details
            ) VALUES (
                to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                'system', NULL, 'create',
                'citizen', NEW.id,
                jsonb_build_object('rfid_uid', NEW.rfid_uid, 'barangay', NEW.barangay)::text
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_citizens_insert
        AFTER INSERT ON citizens
        FOR EACH ROW EXECUTE FUNCTION audit_citizens_insert_fn();
        """
    )
    op.execute(
        """
        CREATE FUNCTION audit_citizens_update_fn() RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO audit_log (
                timestamp, actor_type, actor_id, action,
                object_type, object_id, details
            ) VALUES (
                to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                'system', NULL, 'update',
                'citizen', NEW.id,
                jsonb_build_object('changed', 'see_diff')::text
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_citizens_update
        AFTER UPDATE ON citizens
        FOR EACH ROW
        WHEN (OLD.is_active = NEW.is_active)
        EXECUTE FUNCTION audit_citizens_update_fn();
        """
    )
    op.execute(
        """
        CREATE FUNCTION audit_citizens_soft_delete_fn() RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO audit_log (
                timestamp, actor_type, actor_id, action,
                object_type, object_id, details
            ) VALUES (
                to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                'system', NULL, 'soft_delete',
                'citizen', NEW.id,
                jsonb_build_object('reason', 'erasure_request_or_retention')::text
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_citizens_soft_delete
        AFTER UPDATE ON citizens
        FOR EACH ROW
        WHEN (OLD.is_active = 1 AND NEW.is_active = 0)
        EXECUTE FUNCTION audit_citizens_soft_delete_fn();
        """
    )

    # -------------------------------------------------------------------------
    # sessions audit triggers (insert / status change)
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE FUNCTION audit_sessions_insert_fn() RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO audit_log (
                timestamp, actor_type, actor_id, action,
                object_type, object_id, details
            ) VALUES (
                to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                'citizen', NEW.citizen_id, 'create',
                'session', NEW.id,
                jsonb_build_object('device_id', NEW.device_id)::text
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_sessions_insert
        AFTER INSERT ON sessions
        FOR EACH ROW EXECUTE FUNCTION audit_sessions_insert_fn();
        """
    )
    op.execute(
        """
        CREATE FUNCTION audit_sessions_status_change_fn() RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO audit_log (
                timestamp, actor_type, actor_id, action,
                object_type, object_id, details
            ) VALUES (
                to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                'system', NULL, 'status_change',
                'session', NEW.id,
                jsonb_build_object('from', OLD.status, 'to', NEW.status)::text
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_sessions_status_change
        AFTER UPDATE ON sessions
        FOR EACH ROW
        WHEN (OLD.status IS DISTINCT FROM NEW.status)
        EXECUTE FUNCTION audit_sessions_status_change_fn();
        """
    )

    # -------------------------------------------------------------------------
    # measurements audit trigger (insert)
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE FUNCTION audit_measurements_insert_fn() RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO audit_log (
                timestamp, actor_type, actor_id, action,
                object_type, object_id, details
            ) VALUES (
                to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                'system', NULL, 'create',
                'measurement', NEW.id,
                jsonb_build_object(
                    'session_id', NEW.session_id,
                    'type', NEW.type,
                    'source_device', NEW.source_device,
                    'is_valid', NEW.is_valid
                )::text
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_measurements_insert
        AFTER INSERT ON measurements
        FOR EACH ROW EXECUTE FUNCTION audit_measurements_insert_fn();
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Drop triggers first; functions referenced by them go next.
    op.execute("DROP TRIGGER IF EXISTS audit_measurements_insert ON measurements;")
    op.execute("DROP TRIGGER IF EXISTS audit_sessions_status_change ON sessions;")
    op.execute("DROP TRIGGER IF EXISTS audit_sessions_insert ON sessions;")
    op.execute("DROP TRIGGER IF EXISTS audit_citizens_soft_delete ON citizens;")
    op.execute("DROP TRIGGER IF EXISTS audit_citizens_update ON citizens;")
    op.execute("DROP TRIGGER IF EXISTS audit_citizens_insert ON citizens;")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_delete ON audit_log;")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log;")

    op.execute("DROP FUNCTION IF EXISTS audit_measurements_insert_fn();")
    op.execute("DROP FUNCTION IF EXISTS audit_sessions_status_change_fn();")
    op.execute("DROP FUNCTION IF EXISTS audit_sessions_insert_fn();")
    op.execute("DROP FUNCTION IF EXISTS audit_citizens_soft_delete_fn();")
    op.execute("DROP FUNCTION IF EXISTS audit_citizens_update_fn();")
    op.execute("DROP FUNCTION IF EXISTS audit_citizens_insert_fn();")
    op.execute("DROP FUNCTION IF EXISTS audit_log_no_modify();")

    op.drop_table("schema_version")
    op.drop_index("idx_meas_valid", table_name="measurements")
    op.drop_index("idx_meas_type_time", table_name="measurements")
    op.drop_index("idx_meas_sync", table_name="measurements")
    op.drop_index("idx_meas_session", table_name="measurements")
    op.drop_table("measurements")
    op.drop_index("idx_sessions_sync", table_name="sessions")
    op.drop_index("idx_sessions_status", table_name="sessions")
    op.drop_index("idx_sessions_citizen", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("idx_users_username", table_name="users")
    op.drop_index("idx_users_role_barangay", table_name="users")
    op.drop_table("users")
    op.drop_table("device_config")
    op.drop_index("idx_citizens_rfid", table_name="citizens")
    op.drop_index("idx_citizens_barangay", table_name="citizens")
    op.drop_index("idx_citizens_active_sync", table_name="citizens")
    op.drop_table("citizens")
    op.drop_index("idx_audit_time", table_name="audit_log")
    op.drop_index("idx_audit_object", table_name="audit_log")
    op.drop_index("idx_audit_actor", table_name="audit_log")
    op.drop_table("audit_log")
