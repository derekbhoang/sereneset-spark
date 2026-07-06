import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.asset import AssetFormat, ReviewStatus


class AssetVersionBase(BaseModel):
    version_number: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=180)
    prompt: str = Field(min_length=1)
    model: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=80)
    generation_metadata: dict[str, Any] = Field(default_factory=dict)


class AssetVersionCreate(AssetVersionBase):
    model_config = ConfigDict(extra="forbid")


class AssetVersionInputRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    asset_version_id: uuid.UUID
    role: str
    storage_key: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    created_at: datetime


class AssetVersionRead(AssetVersionBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    asset_id: uuid.UUID
    storage_key: str
    artifact_storage_key: str | None = None
    artifact_filename: str | None = None
    artifact_content_type: str | None = None
    artifact_size_bytes: int | None = None
    inputs: list[AssetVersionInputRead] = Field(default_factory=list)


class AssetVersionDownloadUrl(BaseModel):
    asset_id: uuid.UUID
    version_id: uuid.UUID
    storage_key: str
    download_url: str
    expires_seconds: int


class AssetVersionArtifactDownloadUrl(BaseModel):
    asset_id: uuid.UUID
    version_id: uuid.UUID
    artifact_storage_key: str
    artifact_filename: str | None = None
    artifact_content_type: str | None = None
    artifact_size_bytes: int | None = None
    download_url: str
    expires_seconds: int


class AssetGenerationCreate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=180)
    format: AssetFormat = AssetFormat.image
    channel: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1)
    status: ReviewStatus = ReviewStatus.draft
    reviewer: str | None = Field(default=None, max_length=120)
    tags: list[str] = Field(default_factory=list)
    summary: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    generation_parameters: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = Field(default=None, ge=30)


class AssetVersionGenerationCreate(BaseModel):
    prompt: str = Field(min_length=1)
    label: str | None = Field(default=None, min_length=1, max_length=180)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    generation_parameters: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = Field(default=None, ge=30)


class AssetBase(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    format: AssetFormat
    channel: str = Field(min_length=1, max_length=80)
    status: ReviewStatus = ReviewStatus.draft
    reviewer: str | None = Field(default=None, max_length=120)
    tags: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1)


class AssetCreate(AssetBase):
    initial_version: AssetVersionCreate | None = None


class AssetUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=180)
    format: AssetFormat | None = None
    channel: str | None = Field(default=None, min_length=1, max_length=80)
    status: ReviewStatus | None = None
    reviewer: str | None = Field(default=None, max_length=120)
    tags: list[str] | None = None
    summary: str | None = Field(default=None, min_length=1)


class AssetStatusUpdate(BaseModel):
    status: ReviewStatus


class AssetRead(AssetBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    campaign_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    versions: list[AssetVersionRead] = Field(default_factory=list)
