from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, TYPE_CHECKING

from sqlalchemy import Enum as SqlEnum
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.campaign import Campaign


class AssetFormat(str, Enum):
    copy = "copy"
    image = "image"
    video_concept = "video_concept"


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
