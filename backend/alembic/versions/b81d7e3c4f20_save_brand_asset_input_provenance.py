"""save brand asset input provenance

Revision ID: b81d7e3c4f20
Revises: a72c4e9f1b06
Create Date: 2026-07-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b81d7e3c4f20"
down_revision: Union[str, Sequence[str], None] = "a72c4e9f1b06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add immutable provenance snapshots to version inputs."""
    op.add_column(
        "asset_version_inputs",
        sa.Column(
            "source",
            sa.String(length=40),
            server_default="user_upload",
            nullable=False,
        ),
    )
    op.add_column(
        "asset_version_inputs",
        sa.Column(
            "storage_ownership",
            sa.String(length=40),
            server_default="asset_version",
            nullable=False,
        ),
    )
    op.add_column(
        "asset_version_inputs",
        sa.Column("brand_asset_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "asset_version_inputs",
        sa.Column("campaign_brand_asset_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "asset_version_inputs",
        sa.Column("brand_asset_type", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "asset_version_inputs",
        sa.Column("brand_asset_name", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "asset_version_inputs",
        sa.Column("usage_guidance", sa.Text(), nullable=True),
    )
    op.create_index(
        op.f("ix_asset_version_inputs_source"),
        "asset_version_inputs",
        ["source"],
        unique=False,
    )
    op.create_index(
        op.f("ix_asset_version_inputs_brand_asset_id"),
        "asset_version_inputs",
        ["brand_asset_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_asset_version_inputs_campaign_brand_asset_id"),
        "asset_version_inputs",
        ["campaign_brand_asset_id"],
        unique=False,
    )
    op.alter_column(
        "asset_version_inputs",
        "source",
        server_default=None,
    )
    op.alter_column(
        "asset_version_inputs",
        "storage_ownership",
        server_default=None,
    )


def downgrade() -> None:
    """Remove normalized brand asset provenance snapshots."""
    op.drop_index(
        op.f("ix_asset_version_inputs_campaign_brand_asset_id"),
        table_name="asset_version_inputs",
    )
    op.drop_index(
        op.f("ix_asset_version_inputs_brand_asset_id"),
        table_name="asset_version_inputs",
    )
    op.drop_index(
        op.f("ix_asset_version_inputs_source"),
        table_name="asset_version_inputs",
    )
    op.drop_column("asset_version_inputs", "usage_guidance")
    op.drop_column("asset_version_inputs", "brand_asset_name")
    op.drop_column("asset_version_inputs", "brand_asset_type")
    op.drop_column("asset_version_inputs", "campaign_brand_asset_id")
    op.drop_column("asset_version_inputs", "brand_asset_id")
    op.drop_column("asset_version_inputs", "storage_ownership")
    op.drop_column("asset_version_inputs", "source")
