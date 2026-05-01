"""extend audit_log.actor_type with 'kiosk'

Revision ID: a8c1e3d27f5a
Revises: f566915403f9
Create Date: 2026-05-01 00:00:00.000000

Adds 'kiosk' as an allowed actor_type. This is needed for the kiosk
sync endpoint (POST /api/v1/sync/citizens) to attribute self-service
registrations to the kiosk principal — when a citizen registers at
the touchscreen with no BHW present, the audit row carries
actor_type='kiosk' and actor_id=<device_credentials.device_id>.

Implementation: Postgres CHECK constraints cannot be altered in place,
so we DROP and recreate. The constraint is added/removed without
touching column data, so this is safe to run on populated tables.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "a8c1e3d27f5a"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "f566915403f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_audit_log_actor_type", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_actor_type",
        "audit_log",
        "actor_type IN ('citizen', 'bhw', 'system', 'admin', 'kiosk')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_audit_log_actor_type", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_actor_type",
        "audit_log",
        "actor_type IN ('citizen', 'bhw', 'system', 'admin')",
    )
