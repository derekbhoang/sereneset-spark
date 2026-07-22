from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any, TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime
from sqlalchemy import Enum as SqlEnum
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.campaign import Campaign
    from app.models.generation_job import GenerationJob


class AssetFormat(str, Enum):
    copy = "copy"
    image = "image"
    video_concept = "video_concept"


class AssetInputMediaKind(str, Enum):
    image = "image"
    video = "video"
    document = "document"
    other = "other"


class ReviewStatus(str, Enum):
    draft = "draft"
    in_review = "in_review"
    approved = "approved"
    rejected = "rejected"


def enum_values(enum_class: type[Enum]) -> list[str]:
    return [item.value for item in enum_class]


class Asset(IdMixin, TimestampMixin, Base):
    __tablename__ = "assets"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    format: Mapped[AssetFormat] = mapped_column(
        SqlEnum(
            AssetFormat,
            name="asset_format",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[ReviewStatus] = mapped_column(
        SqlEnum(
            ReviewStatus,
            name="review_status",
            values_callable=enum_values,
        ),
        default=ReviewStatus.draft,
        nullable=False,
        index=True,
    )
    reviewer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(80)),
        default=list,
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="assets")
    versions: Mapped[list["AssetVersion"]] = relationship(
        "AssetVersion",
        back_populates="asset",
        cascade="all, delete-orphan",
        order_by="AssetVersion.version_number.desc()",
    )


class AssetVersion(IdMixin, Base):
    __tablename__ = "asset_versions"
    __table_args__ = (
        UniqueConstraint("asset_id", "version_number", name="uq_asset_version"),
    )

    asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(180), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(600), nullable=False)
    artifact_storage_key: Mapped[str | None] = mapped_column(
        String(600),
        nullable=True,
    )
    artifact_filename: Mapped[str | None] = mapped_column(
        String(240),
        nullable=True,
    )
    artifact_content_type: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )
    artifact_size_bytes: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    generation_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )

    asset: Mapped["Asset"] = relationship("Asset", back_populates="versions")
    inputs: Mapped[list["AssetVersionInput"]] = relationship(
        "AssetVersionInput",
        back_populates="asset_version",
        cascade="all, delete-orphan",
        order_by="AssetVersionInput.created_at.asc()",
    )
    generation_job: Mapped["GenerationJob | None"] = relationship(
        "GenerationJob",
        back_populates="asset_version",
        cascade="all, delete-orphan",
        single_parent=True,
        uselist=False,
    )


class AssetVersionInput(IdMixin, Base):
    __tablename__ = "asset_version_inputs"
    __table_args__ = (
        CheckConstraint(
            "media_kind IN ('image', 'video', 'document', 'other')",
            name="ck_asset_version_inputs_media_kind",
        ),
        CheckConstraint(
            "(source_asset_id IS NULL AND source_version_id IS NULL AND "
            "source_version_number IS NULL) OR "
            "(source_asset_id IS NOT NULL AND source_version_id IS NOT NULL "
            "AND source_version_number > 0)",
            name="ck_asset_version_inputs_source_version_snapshot",
        ),
    )

    asset_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("asset_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(600), nullable=False)
    filename: Mapped[str] = mapped_column(String(240), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    media_kind: Mapped[str] = mapped_column(
        String(20),
        default=AssetInputMediaKind.other.value,
        nullable=False,
        index=True,
    )
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(
        String(40),
        default="user_upload",
        nullable=False,
        index=True,
    )
    storage_ownership: Mapped[str] = mapped_column(
        String(40),
        default="asset_version",
        nullable=False,
    )
    # These are immutable provenance snapshots, not live foreign keys.
    source_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
        index=True,
    )
    source_version_id: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
        index=True,
    )
    source_version_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    brand_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
        index=True,
    )
    campaign_brand_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
        index=True,
    )
    brand_asset_type: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
    )
    brand_asset_name: Mapped[str | None] = mapped_column(
        String(160),
        nullable=True,
    )
    usage_guidance: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    asset_version: Mapped["AssetVersion"] = relationship(
        "AssetVersion",
        back_populates="inputs",
    )
