import uuid
from datetime import datetime
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.asset import ReviewStatus
from app.models.generation_job import GenerationJobKind, GenerationJobStatus
from app.schemas.asset import AssetRead


VideoGenerationTag = Annotated[str, Field(min_length=1, max_length=80)]


class VideoAspectRatio(str, Enum):
    landscape = "16:9"
    portrait = "9:16"
    square = "1:1"


class VideoResolution(str, Enum):
    hd = "720p"
    full_hd = "1080p"


class VideoGenerationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=180)
    channel: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1)
    status: ReviewStatus = ReviewStatus.draft
    reviewer: str | None = Field(default=None, max_length=120)
    tags: list[VideoGenerationTag] = Field(default_factory=list)
    summary: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    duration_seconds: int = Field(default=4, ge=2, le=20)
    aspect_ratio: VideoAspectRatio = VideoAspectRatio.landscape
    resolution: VideoResolution = VideoResolution.hd
    source_version_id: uuid.UUID | None = None


class GenerationJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    asset_version_id: uuid.UUID
    kind: GenerationJobKind
    status: GenerationJobStatus
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1)
    parameters: dict[str, Any]
    progress_percent: int = Field(ge=0, le=100)
    provider_job_id: str | None = Field(default=None, max_length=240)
    attempt_count: int = Field(ge=0)
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class VideoGenerationSubmissionRead(BaseModel):
    asset: AssetRead
    job: GenerationJobRead
