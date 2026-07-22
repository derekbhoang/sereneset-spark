"""extend input provenance

Revision ID: a5d8f1c3b742
Revises: e8c4b7a1d205
Create Date: 2026-07-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a5d8f1c3b742"
down_revision: Union[str, Sequence[str], None] = "e8c4b7a1d205"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add immutable media and source-version provenance snapshots."""
    op.add_column(
        "asset_version_inputs",
        sa.Column(
            "media_kind",
            sa.String(length=20),
            server_default="other",
            nullable=False,
        ),
    )
    op.add_column(
        "asset_version_inputs",
        sa.Column("source_asset_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "asset_version_inputs",
        sa.Column("source_version_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "asset_version_inputs",
        sa.Column("source_version_number", sa.Integer(), nullable=True),
    )
    op.alter_column(
        "asset_version_inputs",
        "sha256",
        existing_type=sa.String(length=64),
        nullable=True,
    )
    op.execute(
        sa.text(
            """
            UPDATE asset_version_inputs
            SET media_kind = CASE
                WHEN lower(content_type) LIKE 'image/%' THEN 'image'
                WHEN lower(content_type) LIKE 'video/%' THEN 'video'
                WHEN lower(content_type) LIKE 'text/%'
                    OR lower(content_type) = 'application/pdf'
                    THEN 'document'
                ELSE 'other'
            END
            """
        )
    )
    op.alter_column(
        "asset_version_inputs",
        "media_kind",
        server_default=None,
    )
    op.create_check_constraint(
        "ck_asset_version_inputs_media_kind",
        "asset_version_inputs",
        "media_kind IN ('image', 'video', 'document', 'other')",
    )
    op.create_check_constraint(
        "ck_asset_version_inputs_source_version_snapshot",
        "asset_version_inputs",
        "(source_asset_id IS NULL AND source_version_id IS NULL AND "
        "source_version_number IS NULL) OR "
        "(source_asset_id IS NOT NULL AND source_version_id IS NOT NULL "
        "AND source_version_number > 0)",
    )
    op.create_index(
        op.f("ix_asset_version_inputs_media_kind"),
        "asset_version_inputs",
        ["media_kind"],
        unique=False,
    )
    op.create_index(
        op.f("ix_asset_version_inputs_source_asset_id"),
        "asset_version_inputs",
        ["source_asset_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_asset_version_inputs_source_version_id"),
        "asset_version_inputs",
        ["source_version_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove media and source-version provenance snapshots."""
    op.drop_index(
        op.f("ix_asset_version_inputs_source_version_id"),
        table_name="asset_version_inputs",
    )
    op.drop_index(
        op.f("ix_asset_version_inputs_source_asset_id"),
        table_name="asset_version_inputs",
    )
    op.drop_index(
        op.f("ix_asset_version_inputs_media_kind"),
        table_name="asset_version_inputs",
    )
    op.drop_constraint(
        "ck_asset_version_inputs_source_version_snapshot",
        "asset_version_inputs",
        type_="check",
    )
    op.drop_constraint(
        "ck_asset_version_inputs_media_kind",
        "asset_version_inputs",
        type_="check",
    )
    op.alter_column(
        "asset_version_inputs",
        "sha256",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.drop_column("asset_version_inputs", "source_version_number")
    op.drop_column("asset_version_inputs", "source_version_id")
    op.drop_column("asset_version_inputs", "source_asset_id")
    op.drop_column("asset_version_inputs", "media_kind")
