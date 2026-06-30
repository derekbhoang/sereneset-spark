"""add asset version artifact fields

Revision ID: 6e2d9b4a1c70
Revises: c98199de6343
Create Date: 2026-06-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6e2d9b4a1c70"
down_revision: Union[str, Sequence[str], None] = "c98199de6343"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "asset_versions",
        sa.Column("artifact_storage_key", sa.String(length=600), nullable=True),
    )
    op.add_column(
        "asset_versions",
        sa.Column("artifact_filename", sa.String(length=240), nullable=True),
    )
    op.add_column(
        "asset_versions",
        sa.Column("artifact_content_type", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "asset_versions",
        sa.Column("artifact_size_bytes", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("asset_versions", "artifact_size_bytes")
    op.drop_column("asset_versions", "artifact_content_type")
    op.drop_column("asset_versions", "artifact_filename")
    op.drop_column("asset_versions", "artifact_storage_key")
