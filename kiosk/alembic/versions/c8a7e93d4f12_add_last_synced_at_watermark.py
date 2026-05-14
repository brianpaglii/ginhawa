"""add last_synced_at watermark column for sync resync

Revision ID: c8a7e93d4f12
Revises: 9a4b1c5d2e7f
Create Date: 2026-05-14 00:00:00.000000

Per ADR-0024 / docs/audits/2026-05-14-session-sync-create-update-gap-audit.md.

The sync daemon's row-selection moves from ``WHERE synced=0`` (INSERT-
once semantics) to a watermark predicate:

    last_synced_at IS NULL OR last_synced_at < updated_at

Rows with ``last_synced_at=NULL`` are "never synced" — the daemon
picks them up on the next cycle, the cloud's existing upsert path
([cloud/src/ginhawa_cloud/api/sync_sessions.py:99-139]) handles
them via _apply_update (the rows already exist on the cloud from
their original create-time push). This is the intentional one-shot
backfill that surfaces every stale session row at first sync after
deployment — the bench observed 58 of 58 completed kiosk sessions
showing status='in_progress' in the cloud; the backfill flips them
all to 'completed' in 1-2 cycles.

The legacy ``synced`` column stays in place. The daemon no longer
consults it (the new predicate uses ``last_synced_at`` only), but
``_apply_results`` still sets ``synced=1`` for backward-compat with
any reader that hasn't been updated. ADR-0024 documents ``synced``
as deprecated; a follow-up PR will remove the column.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8a7e93d4f12"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = (
    "9a4b1c5d2e7f"  # pragma: allowlist secret
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # All three synced tables get the same nullable column with no
    # default. Existing rows get NULL (the daemon's predicate treats
    # NULL as "needs sync"), new rows default to NULL until the
    # daemon stamps after first successful upload.
    op.add_column(
        "citizens",
        sa.Column("last_synced_at", sa.String(), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column("last_synced_at", sa.String(), nullable=True),
    )
    op.add_column(
        "measurements",
        sa.Column("last_synced_at", sa.String(), nullable=True),
    )

    # Composite indexes supporting the daemon's pending-row query.
    # The leading column is ``last_synced_at`` so rows with NULL
    # cluster first; the trailing column is the table's "freshness"
    # signal (updated_at for sessions/measurements/citizens) so the
    # predicate ``last_synced_at < updated_at`` is range-scannable.
    op.create_index(
        "idx_sessions_resync",
        "sessions",
        ["last_synced_at", "updated_at"],
    )
    op.create_index(
        "idx_citizens_resync",
        "citizens",
        ["last_synced_at", "updated_at"],
    )
    op.create_index(
        "idx_measurements_resync",
        "measurements",
        ["last_synced_at", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_measurements_resync", table_name="measurements")
    op.drop_index("idx_citizens_resync", table_name="citizens")
    op.drop_index("idx_sessions_resync", table_name="sessions")
    op.drop_column("measurements", "last_synced_at")
    op.drop_column("sessions", "last_synced_at")
    op.drop_column("citizens", "last_synced_at")
