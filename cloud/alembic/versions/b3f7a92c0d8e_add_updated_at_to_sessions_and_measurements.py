"""add updated_at to sessions and measurements

Revision ID: b3f7a92c0d8e
Revises: a8c1e3d27f5a
Create Date: 2026-05-01 00:30:00.000000

The kiosk sync endpoints (POST /api/v1/sync/sessions and
/sync/measurements) need a per-row updated_at to drive idempotency:
unknown id -> created; known id with newer updated_at -> updated;
known id with same/older updated_at -> conflict_stale. That mirrors
the citizens.updated_at column established in the initial schema.

The column is NOT NULL with a server default of "now in ISO 8601 UTC",
matching the convention used by every other timestamp column in the
schema. Existing rows backfill to the time of migration, which is
fine because the kiosk's first re-sync will overwrite with the row's
true latest timestamp.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3f7a92c0d8e"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "a8c1e3d27f5a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ISO_UTC_NOW = """to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')"""


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "updated_at",
            sa.String(),
            nullable=False,
            server_default=sa.text(_ISO_UTC_NOW),
        ),
    )
    op.add_column(
        "measurements",
        sa.Column(
            "updated_at",
            sa.String(),
            nullable=False,
            server_default=sa.text(_ISO_UTC_NOW),
        ),
    )


def downgrade() -> None:
    op.drop_column("measurements", "updated_at")
    op.drop_column("sessions", "updated_at")
