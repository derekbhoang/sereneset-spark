from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.asset import AssetVersion


class GenerationJobKind(str, Enum):
    video = "video"


class GenerationJobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


class GenerationJob(IdMixin, TimestampMixin, Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        UniqueConstraint(
            "asset_version_id",
            name="uq_generation_job_asset_version",
        ),
        CheckConstraint(
            "kind IN ('video')",
            name="ck_generation_jobs_kind",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')",
            name="ck_generation_jobs_status",
        ),
        CheckConstraint(
            "progress_percent BETWEEN 0 AND 100",
            name="ck_generation_jobs_progress_percent",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_generation_jobs_attempt_count",
        ),
        Index(
            "ix_generation_jobs_status_created_at",
            "status",
            "created_at",
        ),
    )

    asset_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("asset_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(
        String(40),
        default=GenerationJobKind.video.value,
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(40),
        default=GenerationJobStatus.queued.value,
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )
    progress_percent: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    provider_job_id: Mapped[str | None] = mapped_column(
        String(240),
        nullable=True,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    asset_version: Mapped["AssetVersion"] = relationship(
        "AssetVersion",
        back_populates="generation_job",
    )
