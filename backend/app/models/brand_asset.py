from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime
from sqlalchemy import Enum as SqlEnum
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.campaign import Campaign


def enum_values(enum_class: type[Enum]) -> list[str]:
    return [item.value for item in enum_class]


class BrandAssetType(str, Enum):
    logo = "logo"
    product_image = "product_image"
    style_reference = "style_reference"
    guideline = "guideline"
    font = "font"
    other = "other"


class BrandAsset(IdMixin, TimestampMixin, Base):
    __tablename__ = "brand_assets"

    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    asset_type: Mapped[BrandAssetType] = mapped_column(
        SqlEnum(
            BrandAssetType,
            name="brand_asset_type",
            values_callable=enum_values,
        ),
        nullable=False,
        index=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_guidance: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_key: Mapped[str] = mapped_column(String(600), nullable=False)
    filename: Mapped[str] = mapped_column(String(240), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(80)),
        default=list,
        nullable=False,
    )
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        index=True,
    )

    campaign_links: Mapped[list["CampaignBrandAsset"]] = relationship(
        "CampaignBrandAsset",
        back_populates="brand_asset",
        cascade="all, delete-orphan",
        order_by="CampaignBrandAsset.created_at.desc()",
    )


class CampaignBrandAsset(IdMixin, Base):
    __tablename__ = "campaign_brand_assets"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id",
            "brand_asset_id",
            "role",
            name="uq_campaign_brand_asset_role",
        ),
    )

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    brand_asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("brand_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    campaign: Mapped["Campaign"] = relationship(
        "Campaign",
        back_populates="brand_asset_links",
    )
    brand_asset: Mapped["BrandAsset"] = relationship(
        "BrandAsset",
        back_populates="campaign_links",
    )
