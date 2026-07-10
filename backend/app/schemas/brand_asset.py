import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.brand_asset import BrandAssetType


BrandAssetTag = Annotated[str, Field(min_length=1, max_length=80)]
CampaignBrandAssetRole = Annotated[
    str,
    Field(
        min_length=1,
        max_length=40,
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
    ),
]


class BrandAssetBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=160)
    asset_type: BrandAssetType
    description: str | None = None
    usage_guidance: str | None = None
    tags: list[BrandAssetTag] = Field(default_factory=list)
    source_url: str | None = Field(default=None, max_length=1000)


class BrandAssetCreate(BrandAssetBase):
    model_config = ConfigDict(extra="forbid")


class BrandAssetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=160)
    asset_type: BrandAssetType | None = None
    description: str | None = None
    usage_guidance: str | None = None
    tags: list[BrandAssetTag] | None = None
    source_url: str | None = Field(default=None, max_length=1000)
    is_active: bool | None = None

    @field_validator("name", "asset_type", "tags", "is_active")
    @classmethod
    def validate_non_nullable_updates(cls, value: object) -> object:
        if value is None:
            raise ValueError("Field must not be null when provided")

        return value


class BrandAssetRead(BrandAssetBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    storage_key: str = Field(max_length=600)
    filename: str = Field(max_length=240)
    content_type: str = Field(max_length=120)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    is_active: bool
    created_at: datetime
    updated_at: datetime


class BrandAssetDownloadUrl(BaseModel):
    brand_asset_id: uuid.UUID
    storage_key: str
    filename: str
    content_type: str
    size_bytes: int = Field(ge=0)
    download_url: str
    expires_seconds: int = Field(gt=0)


class CampaignBrandAssetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    brand_asset_id: uuid.UUID
    role: CampaignBrandAssetRole = "reference"


class CampaignBrandAssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    campaign_id: uuid.UUID
    brand_asset_id: uuid.UUID
    role: CampaignBrandAssetRole
    created_at: datetime
    brand_asset: BrandAssetRead
