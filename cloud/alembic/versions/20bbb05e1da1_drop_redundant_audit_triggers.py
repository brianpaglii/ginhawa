"""drop redundant audit triggers

Revision ID: 20bbb05e1da1
Revises: c62cbc71004c
Create Date: 2026-04-28 15:39:43.361523

The application layer is now the single writer for audit_log entries on
mutations of citizens / sessions / measurements (via
``ginhawa_cloud.services.audit.record_audit``). The database triggers that
previously double-wrote those rows are removed here.

The append-only enforcement triggers ``audit_log_no_update`` and
``audit_log_no_delete`` remain — they don't write rows, they just block
mutation of the audit table.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa  # noqa: F401  imported for symmetry with peer revisions


revision: str = "20bbb05e1da1"
down_revision: Union[str, Sequence[str], None] = "c62cbc71004c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the six mutation-audit triggers and their backing functions."""
    op.execute("DROP TRIGGER IF EXISTS audit_measurements_insert ON measurements;")
    op.execute("DROP TRIGGER IF EXISTS audit_sessions_status_change ON sessions;")
    op.execute("DROP TRIGGER IF EXISTS audit_sessions_insert ON sessions;")
    op.execute("DROP TRIGGER IF EXISTS audit_citizens_soft_delete ON citizens;")
    op.execute("DROP TRIGGER IF EXISTS audit_citizens_update ON citizens;")
    op.execute("DROP TRIGGER IF EXISTS audit_citizens_insert ON citizens;")

    op.execute("DROP FUNCTION IF EXISTS audit_measurements_insert_fn();")
    op.execute("DROP FUNCTION IF EXISTS audit_sessions_status_change_fn();")
    op.execute("DROP FUNCTION IF EXISTS audit_sessions_insert_fn();")
    op.execute("DROP FUNCTION IF EXISTS audit_citizens_soft_delete_fn();")
    op.execute("DROP FUNCTION IF EXISTS audit_citizens_update_fn();")
    op.execute("DROP FUNCTION IF EXISTS audit_citizens_insert_fn();")


def downgrade() -> None:
    """Recreate the six trigger functions and triggers as they were."""
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
