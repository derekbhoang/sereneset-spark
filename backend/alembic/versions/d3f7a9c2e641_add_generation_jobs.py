"""add generation jobs

Revision ID: d3f7a9c2e641
Revises: b81d7e3c4f20
Create Date: 2026-07-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "d3f7a9c2e641"
down_revision: Union[str, Sequence[str], None] = "b81d7e3c4f20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create durable generation jobs for asynchronous media work."""
    op.create_table(
        "generation_jobs",
        sa.Column("asset_version_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("progress_percent", sa.Integer(), nullable=False),
        sa.Column("provider_job_id", sa.String(length=240), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_generation_jobs_attempt_count",
        ),
        sa.CheckConstraint(
            "kind IN ('video')",
            name="ck_generation_jobs_kind",
        ),
        sa.CheckConstraint(
            "progress_percent BETWEEN 0 AND 100",
            name="ck_generation_jobs_progress_percent",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')",
            name="ck_generation_jobs_status",
        ),
        sa.ForeignKeyConstraint(
            ["asset_version_id"],
            ["asset_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "asset_version_id",
            name="uq_generation_job_asset_version",
        ),
    )
    op.create_index(
        op.f("ix_generation_jobs_kind"),
        "generation_jobs",
        ["kind"],
        unique=False,
    )
    op.create_index(
        op.f("ix_generation_jobs_provider_job_id"),
        "generation_jobs",
        ["provider_job_id"],
        unique=False,
    )
    op.create_index(
        "ix_generation_jobs_status_created_at",
        "generation_jobs",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove asynchronous generation jobs."""
    op.drop_index(
        "ix_generation_jobs_status_created_at",
        table_name="generation_jobs",
    )
    op.drop_index(
        op.f("ix_generation_jobs_provider_job_id"),
        table_name="generation_jobs",
    )
    op.drop_index(
        op.f("ix_generation_jobs_kind"),
        table_name="generation_jobs",
    )
    op.drop_table("generation_jobs")
