"""add asset version inputs

Revision ID: f4a8c2b91d3e
Revises: 6e2d9b4a1c70
Create Date: 2026-07-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f4a8c2b91d3e"
down_revision: Union[str, Sequence[str], None] = "6e2d9b4a1c70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "asset_version_inputs",
        sa.Column("asset_version_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("storage_key", sa.String(length=600), nullable=False),
        sa.Column("filename", sa.String(length=240), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_version_id"],
            ["asset_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_asset_version_inputs_asset_version_id"),
        "asset_version_inputs",
        ["asset_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_asset_version_inputs_role"),
        "asset_version_inputs",
        ["role"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_asset_version_inputs_role"),
        table_name="asset_version_inputs",
    )
    op.drop_index(
        op.f("ix_asset_version_inputs_asset_version_id"),
        table_name="asset_version_inputs",
    )
    op.drop_table("asset_version_inputs")
