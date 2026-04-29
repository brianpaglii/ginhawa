"""add device_credentials table

Revision ID: f566915403f9
Revises: 20bbb05e1da1
Create Date: 2026-04-30 00:43:15.343113

Adds the device_credentials table that backs kiosk-to-cloud sync
authentication. One row per kiosk; the plaintext API key is shown
once at creation and never stored — only the argon2id hash is
persisted (mirroring users.password_hash). Revocation is the
soft-delete pathway: revoked_at + revoked_by are set on revoke;
reactivation is intentionally not supported.

Autogenerate also flagged schema_version as a "missing model" and
proposed dropping it. That's a false positive — schema_version is
created via raw op.execute() in the initial migration and has no
ORM model. The drop has been removed from upgrade() and the
matching CREATE removed from downgrade().
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f566915403f9"
down_revision: Union[str, Sequence[str], None] = "20bbb05e1da1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Same Postgres-flavoured ISO 8601 UTC timestamp expression used
# everywhere else in the schema (matches the SQLite datetime('now')
# semantics in schema.sql).
_ISO_UTC_NOW = """to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "device_credentials",
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("api_key_hash", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.String(),
            nullable=False,
            server_default=sa.text(_ISO_UTC_NOW),
        ),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("revoked_at", sa.String(), nullable=True),
        sa.Column("revoked_by", sa.String(), nullable=True),
        sa.Column("last_seen_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("device_id"),
    )
    op.create_index(
        "idx_device_credentials_description",
        "device_credentials",
        ["description"],
        unique=True,
    )
    op.create_index(
        "idx_device_credentials_revoked_at",
        "device_credentials",
        ["revoked_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "idx_device_credentials_revoked_at",
        table_name="device_credentials",
    )
    op.drop_index(
        "idx_device_credentials_description",
        table_name="device_credentials",
    )
    op.drop_table("device_credentials")
