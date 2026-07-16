"""add worker heartbeats

Revision ID: e8c4b7a1d205
Revises: d3f7a9c2e641
Create Date: 2026-07-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e8c4b7a1d205"
down_revision: Union[str, Sequence[str], None] = "d3f7a9c2e641"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the worker readiness heartbeat table."""
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_name", sa.String(length=80), nullable=False),
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("worker_name"),
    )


def downgrade() -> None:
    """Remove worker readiness heartbeats."""
    op.drop_table("worker_heartbeats")
