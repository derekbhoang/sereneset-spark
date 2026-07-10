"""add brand assets and campaign links

Revision ID: a72c4e9f1b06
Revises: f4a8c2b91d3e
Create Date: 2026-07-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "a72c4e9f1b06"
down_revision: Union[str, Sequence[str], None] = "f4a8c2b91d3e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create reusable brand assets and their campaign links."""
    op.create_table(
        "brand_assets",
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "asset_type",
            sa.Enum(
                "logo",
                "product_image",
                "style_reference",
                "guideline",
                "font",
                "other",
                name="brand_asset_type",
            ),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("usage_guidance", sa.Text(), nullable=True),
        sa.Column("storage_key", sa.String(length=600), nullable=False),
        sa.Column("filename", sa.String(length=240), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String(length=80)),
            nullable=False,
        ),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_brand_assets_name"),
        "brand_assets",
        ["name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_brand_assets_asset_type"),
        "brand_assets",
        ["asset_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_brand_assets_is_active"),
        "brand_assets",
        ["is_active"],
        unique=False,
    )

    op.create_table(
        "campaign_brand_assets",
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("brand_asset_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["brand_asset_id"],
            ["brand_assets.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "campaign_id",
            "brand_asset_id",
            "role",
            name="uq_campaign_brand_asset_role",
        ),
    )
    op.create_index(
        op.f("ix_campaign_brand_assets_campaign_id"),
        "campaign_brand_assets",
        ["campaign_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_campaign_brand_assets_brand_asset_id"),
        "campaign_brand_assets",
        ["brand_asset_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_campaign_brand_assets_role"),
        "campaign_brand_assets",
        ["role"],
        unique=False,
    )


def downgrade() -> None:
    """Remove campaign links and reusable brand assets."""
    op.drop_index(
        op.f("ix_campaign_brand_assets_role"),
        table_name="campaign_brand_assets",
    )
    op.drop_index(
        op.f("ix_campaign_brand_assets_brand_asset_id"),
        table_name="campaign_brand_assets",
    )
    op.drop_index(
        op.f("ix_campaign_brand_assets_campaign_id"),
        table_name="campaign_brand_assets",
    )
    op.drop_table("campaign_brand_assets")

    op.drop_index(
        op.f("ix_brand_assets_is_active"),
        table_name="brand_assets",
    )
    op.drop_index(
        op.f("ix_brand_assets_asset_type"),
        table_name="brand_assets",
    )
    op.drop_index(
        op.f("ix_brand_assets_name"),
        table_name="brand_assets",
    )
    op.drop_table("brand_assets")
    sa.Enum(name="brand_asset_type").drop(op.get_bind(), checkfirst=True)
