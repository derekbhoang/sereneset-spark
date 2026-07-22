import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models.asset import Asset, AssetFormat, AssetVersion
from app.models.brand_asset import BrandAsset, CampaignBrandAsset
from app.models.campaign import Campaign
from app.models.generation_job import (
    GenerationJob,
    GenerationJobKind,
    GenerationJobStatus,
)
from app.schemas.asset import AssetRead
from app.schemas.generation_job import (
    GenerationJobRead,
    VideoGenerationCreate,
    VideoGenerationSubmissionRead,
)
from app.services.generation import (
    GenerationInputError,
    VIDEO_SOURCE_INPUT_ROLE,
    VideoInputMode,
    format_size_limit,
    infer_asset_media_type,
    optional_string,
    validate_video_generation_parameters,
    validate_video_input_assets,
)
from app.services.input_provenance import (
    build_asset_version_input,
    infer_input_media_kind,
)
from app.services.storage import (
    B2StorageService,
    FileObjectInspection,
    StorageConfigurationError,
    StorageObjectTooLargeError,
    StorageOperationError,
    build_asset_version_input_storage_key,
    build_asset_version_storage_key,
    get_storage_service,
    normalize_artifact_filename,
)
from app.services.video_validation import (
    Mp4ValidationResult,
    VideoContentValidationError,
    validate_mp4_contents,
)


router = APIRouter(tags=["generation-jobs"])
VIDEO_UPLOAD_CONTENT_TYPES_BY_SUFFIX = {".mp4": "video/mp4"}


class VideoSourceOrigin(str, Enum):
    none = "none"
    asset_version = "asset_version"
    brand_asset = "brand_asset"
    user_upload = "user_upload"


@dataclass(frozen=True)
class ResolvedVideoSource:
    origin: VideoSourceOrigin
    input_record: dict[str, object] | None = None
    source_version_id: uuid.UUID | None = None
    source_brand_asset_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        has_input = self.input_record is not None
        if (
            self.origin == VideoSourceOrigin.none
            and has_input
        ) or (
            self.origin != VideoSourceOrigin.none
            and not has_input
        ):
            raise ValueError(
                "Resolved video source must match its source origin"
            )
        if (
            self.origin == VideoSourceOrigin.asset_version
            and (
                self.source_version_id is None
                or self.source_brand_asset_id is not None
            )
        ):
            raise ValueError("Asset-version source must include its version ID")
        if (
            self.origin == VideoSourceOrigin.brand_asset
            and (
                self.source_brand_asset_id is None
                or self.source_version_id is not None
            )
        ):
            raise ValueError("Brand-asset source must include its asset ID")
        if self.origin in {
            VideoSourceOrigin.none,
            VideoSourceOrigin.user_upload,
        } and (
            self.source_version_id is not None
            or self.source_brand_asset_id is not None
        ):
            raise ValueError(
                "Video source origin cannot include a stored-source ID"
            )

    @property
    def input_assets(self) -> list[dict[str, object]]:
        return [self.input_record] if self.input_record is not None else []

    @property
    def excluded_context_brand_asset_id(self) -> uuid.UUID | None:
        return (
            self.source_brand_asset_id
            if self.origin == VideoSourceOrigin.brand_asset
            else None
        )

    def as_metadata(self) -> dict[str, object | None]:
        return {
            "origin": self.origin.value,
            "source_version_id": (
                str(self.source_version_id)
                if self.source_version_id is not None
                else None
            ),
            "source_brand_asset_id": (
                str(self.source_brand_asset_id)
                if self.source_brand_asset_id is not None
                else None
            ),
        }


def metadata_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def metadata_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []

    return [item for item in value if isinstance(item, dict)]


def parse_video_generation_payload(payload: str) -> VideoGenerationCreate:
    try:
        return VideoGenerationCreate.model_validate_json(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc


def normalize_video_upload_filename(file: UploadFile) -> str:
    try:
        return normalize_artifact_filename(file.filename or "")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc).replace("Artifact", "Source video"),
        ) from exc


def normalize_video_upload_content_type(
    *,
    file: UploadFile,
    filename: str,
) -> str:
    suffix = PurePosixPath(filename).suffix.casefold()
    content_type = VIDEO_UPLOAD_CONTENT_TYPES_BY_SUFFIX.get(suffix)
    if content_type is None:
        supported_extensions = ", ".join(sorted(VIDEO_UPLOAD_CONTENT_TYPES_BY_SUFFIX))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Source video file type is not supported. "
                f"Use one of: {supported_extensions}"
            ),
        )

    declared_content_type = (
        (file.content_type or "").split(";", maxsplit=1)[0].strip().casefold()
    )
    allowed_declared_types = {"", "application/octet-stream", content_type}
    if declared_content_type not in allowed_declared_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Source video must use content type '{content_type}'",
        )

    return content_type


def inspect_video_upload(
    *,
    storage: B2StorageService,
    file: UploadFile,
    max_size_bytes: int,
) -> FileObjectInspection:
    try:
        return storage.inspect_fileobj(
            fileobj=file.file,
            max_size_bytes=max_size_bytes,
        )
    except StorageObjectTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                "Source video must be "
                f"{format_size_limit(max_size_bytes)} or smaller"
            ),
        ) from exc
    except StorageOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


def validate_video_upload_contents(
    *,
    file: UploadFile,
    inspection: FileObjectInspection,
) -> Mp4ValidationResult:
    try:
        return validate_mp4_contents(
            fileobj=file.file,
            size_bytes=inspection.size,
        )
    except VideoContentValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Source video is not a valid MP4: {exc}",
        ) from exc


def delete_uploaded_video_safely(
    *,
    storage: B2StorageService,
    storage_key: str,
) -> None:
    try:
        storage.delete_object(key=storage_key)
    except (StorageConfigurationError, BotoCoreError, ClientError):
        pass


def campaign_generation_job_statement(
    *,
    campaign_id: uuid.UUID,
    job_id: uuid.UUID | None = None,
    status_filter: GenerationJobStatus | None = None,
    for_update: bool = False,
):
    statement = (
        select(GenerationJob)
        .join(GenerationJob.asset_version)
        .join(AssetVersion.asset)
        .where(Asset.campaign_id == campaign_id)
    )
    if job_id is not None:
        statement = statement.where(GenerationJob.id == job_id)
    if status_filter is not None:
        statement = statement.where(
            GenerationJob.status == status_filter.value
        )
    if for_update:
        statement = statement.with_for_update(of=GenerationJob)

    return statement


def get_campaign_generation_job_or_404(
    *,
    campaign_id: uuid.UUID,
    job_id: uuid.UUID,
    db: Session,
    for_update: bool = False,
) -> GenerationJob:
    job = db.scalar(
        campaign_generation_job_statement(
            campaign_id=campaign_id,
            job_id=job_id,
            for_update=for_update,
        )
    )
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generation job not found",
        )

    return job


def generation_job_metadata_record(
    job: GenerationJob,
) -> dict[str, object | None]:
    return {
        "id": str(job.id),
        "kind": job.kind,
        "status": job.status,
        "progress_percent": job.progress_percent,
        "provider_job_id": job.provider_job_id,
        "attempt_count": job.attempt_count,
        "error_message": job.error_message,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": (
            job.completed_at.isoformat() if job.completed_at else None
        ),
    }


def sync_generation_job_transition_metadata(
    *,
    job: GenerationJob,
    event: str,
    previous_status: str,
    recorded_at: datetime,
) -> None:
    version = job.asset_version
    metadata = dict(version.generation_metadata or {})
    job_record = generation_job_metadata_record(job)
    transition_history = metadata_list(metadata.get("job_transitions"))
    transition_history = transition_history[-19:]
    transition_history.append(
        {
            "event": event,
            "previous_status": previous_status,
            "status": job.status,
            "attempt_count": job.attempt_count,
            "recorded_at": recorded_at.isoformat(),
            "source": "api",
        }
    )
    metadata["job"] = job_record
    metadata["job_transitions"] = transition_history

    provenance = metadata.get("provenance")
    if isinstance(provenance, dict):
        metadata["provenance"] = {
            **provenance,
            "job": job_record,
            "job_transitions": transition_history,
        }

    if event == "canceled":
        metadata.pop("failure", None)
        metadata["cancellation"] = {
            "recorded_at": recorded_at.isoformat(),
            "source": "api",
        }
    elif event == "retried":
        metadata.pop("failure", None)
        metadata.pop("cancellation", None)

    version.generation_metadata = metadata
    version.asset.updated_at = recorded_at


def get_campaign_or_404(campaign_id: uuid.UUID, db: Session) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )

    return campaign


def get_source_version_or_404(
    *,
    campaign_id: uuid.UUID,
    source_version_id: uuid.UUID,
    db: Session,
) -> AssetVersion:
    statement = (
        select(AssetVersion)
        .join(AssetVersion.asset)
        .options(selectinload(AssetVersion.asset))
        .where(
            AssetVersion.id == source_version_id,
            Asset.campaign_id == campaign_id,
        )
    )
    source_version = db.scalar(statement)
    if source_version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source asset version not found in campaign",
        )

    if source_version.asset.format not in {
        AssetFormat.image,
        AssetFormat.video_concept,
    }:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Video source version must belong to an image or video asset",
        )

    if source_version.artifact_storage_key is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Video source version does not have a stored artifact",
        )

    if not source_version.artifact_filename:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Video source artifact does not have a filename",
        )

    if not source_version.artifact_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Video source artifact does not have a positive size",
        )

    return source_version


def source_version_sha256(source_version: AssetVersion) -> str | None:
    metadata = source_version.generation_metadata or {}
    artifact_flow = metadata_dict(metadata.get("artifact_flow"))
    source_sha256 = optional_string(artifact_flow.get("source_sha256"))
    if source_sha256:
        return source_sha256

    for asset in metadata_list(metadata.get("assets")):
        sha256 = optional_string(asset.get("sha256"))
        if sha256:
            return sha256

    return None


def source_version_input_record(
    source_version: AssetVersion,
) -> dict[str, object]:
    filename = source_version.artifact_filename or "source-media"
    content_type = infer_asset_media_type(
        content_type=source_version.artifact_content_type,
        filename=filename,
        url=None,
    )
    return {
        "role": "source_creative",
        "storage_key": source_version.artifact_storage_key,
        "filename": filename,
        "content_type": content_type,
        "media_kind": infer_input_media_kind(content_type).value,
        "size_bytes": source_version.artifact_size_bytes,
        "sha256": source_version_sha256(source_version),
        "source": "source_version_artifact",
        "storage_ownership": "source_asset_version",
        "source_asset_id": str(source_version.asset_id),
        "source_version_id": str(source_version.id),
        "source_version_number": source_version.version_number,
    }


def get_source_brand_asset_link_or_404(
    *,
    campaign_id: uuid.UUID,
    source_brand_asset_id: uuid.UUID,
    db: Session,
) -> CampaignBrandAsset:
    statement = (
        select(CampaignBrandAsset)
        .join(CampaignBrandAsset.brand_asset)
        .options(selectinload(CampaignBrandAsset.brand_asset))
        .where(
            CampaignBrandAsset.campaign_id == campaign_id,
            CampaignBrandAsset.brand_asset_id == source_brand_asset_id,
            BrandAsset.is_active.is_(True),
        )
        .order_by(CampaignBrandAsset.created_at.asc())
        .limit(1)
    )
    link = db.scalar(statement)
    if link is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source brand asset is not attached to this campaign",
        )

    return link


def campaign_brand_asset_input_record(
    link: CampaignBrandAsset,
    *,
    role: str | None = None,
) -> dict[str, object]:
    brand_asset = link.brand_asset
    return {
        "role": role or link.role,
        "storage_key": brand_asset.storage_key,
        "filename": brand_asset.filename,
        "content_type": brand_asset.content_type,
        "media_kind": infer_input_media_kind(brand_asset.content_type).value,
        "size_bytes": brand_asset.size_bytes,
        "sha256": brand_asset.sha256,
        "source": "campaign_brand_asset",
        "storage_ownership": "brand_asset",
        "brand_asset_id": str(brand_asset.id),
        "campaign_brand_asset_id": str(link.id),
        "brand_asset_type": brand_asset.asset_type.value,
        "brand_asset_name": brand_asset.name,
        "usage_guidance": brand_asset.usage_guidance,
    }


def campaign_brand_context_assets(
    *,
    campaign_id: uuid.UUID,
    db: Session,
    exclude_brand_asset_id: uuid.UUID | None = None,
) -> list[dict[str, object]]:
    statement = (
        select(CampaignBrandAsset)
        .join(CampaignBrandAsset.brand_asset)
        .options(selectinload(CampaignBrandAsset.brand_asset))
        .where(
            CampaignBrandAsset.campaign_id == campaign_id,
            BrandAsset.is_active.is_(True),
        )
        .order_by(CampaignBrandAsset.created_at.asc())
    )
    if exclude_brand_asset_id is not None:
        statement = statement.where(
            CampaignBrandAsset.brand_asset_id != exclude_brand_asset_id
        )

    links = list(db.scalars(statement).all())
    return [campaign_brand_asset_input_record(link) for link in links]


def uploaded_video_input_record(
    *,
    storage_key: str,
    filename: str,
    content_type: str,
    inspection: FileObjectInspection,
    content_validation: Mp4ValidationResult,
) -> dict[str, object]:
    return {
        "role": VIDEO_SOURCE_INPUT_ROLE,
        "storage_key": storage_key,
        "filename": filename,
        "content_type": content_type,
        "media_kind": infer_input_media_kind(content_type).value,
        "size_bytes": inspection.size,
        "sha256": inspection.sha256,
        "source": "user_upload",
        "storage_ownership": "asset_version",
        "content_validation": content_validation.as_metadata(),
    }


def resolve_video_source(
    *,
    campaign_id: uuid.UUID,
    video_in: VideoGenerationCreate,
    db: Session,
    uploaded_input: dict[str, object] | None = None,
) -> ResolvedVideoSource:
    if uploaded_input is not None:
        if (
            video_in.source_version_id is not None
            or video_in.source_brand_asset_id is not None
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "An uploaded source video cannot be combined with a stored "
                    "source version or brand asset"
                ),
            )

        return ResolvedVideoSource(
            origin=VideoSourceOrigin.user_upload,
            input_record=uploaded_input,
        )

    if video_in.source_version_id is not None:
        source_version = get_source_version_or_404(
            campaign_id=campaign_id,
            source_version_id=video_in.source_version_id,
            db=db,
        )
        return ResolvedVideoSource(
            origin=VideoSourceOrigin.asset_version,
            input_record=source_version_input_record(source_version),
            source_version_id=source_version.id,
        )

    if video_in.source_brand_asset_id is not None:
        source_brand_asset_link = get_source_brand_asset_link_or_404(
            campaign_id=campaign_id,
            source_brand_asset_id=video_in.source_brand_asset_id,
            db=db,
        )
        return ResolvedVideoSource(
            origin=VideoSourceOrigin.brand_asset,
            input_record=campaign_brand_asset_input_record(
                source_brand_asset_link,
                role=VIDEO_SOURCE_INPUT_ROLE,
            ),
            source_brand_asset_id=video_in.source_brand_asset_id,
        )

    return ResolvedVideoSource(origin=VideoSourceOrigin.none)


def validate_resolved_video_source(source: ResolvedVideoSource) -> None:
    if source.origin == VideoSourceOrigin.none:
        return

    record = source.input_record
    if record is None:
        raise GenerationInputError("Resolved video source is missing its input record")

    expected_provenance = {
        VideoSourceOrigin.asset_version: (
            "source_version_artifact",
            "source_asset_version",
        ),
        VideoSourceOrigin.brand_asset: (
            "campaign_brand_asset",
            "brand_asset",
        ),
        VideoSourceOrigin.user_upload: (
            "user_upload",
            "asset_version",
        ),
    }
    expected_source, expected_ownership = expected_provenance[source.origin]
    if optional_string(record.get("source")) != expected_source:
        raise GenerationInputError(
            "Resolved video source has inconsistent provenance"
        )
    if optional_string(record.get("storage_ownership")) != expected_ownership:
        raise GenerationInputError(
            "Resolved video source has inconsistent storage ownership"
        )

    if source.origin == VideoSourceOrigin.asset_version:
        if optional_string(record.get("source_version_id")) != str(
            source.source_version_id
        ):
            raise GenerationInputError(
                "Resolved video source version ID does not match its provenance"
            )
        if optional_string(record.get("source_asset_id")) is None:
            raise GenerationInputError(
                "Resolved video source is missing its source asset ID"
            )
        source_version_number = record.get("source_version_number")
        if (
            not isinstance(source_version_number, int)
            or isinstance(source_version_number, bool)
            or source_version_number < 1
        ):
            raise GenerationInputError(
                "Resolved video source has an invalid source version number"
            )

    if source.origin == VideoSourceOrigin.brand_asset:
        if optional_string(record.get("brand_asset_id")) != str(
            source.source_brand_asset_id
        ):
            raise GenerationInputError(
                "Resolved video brand asset ID does not match its provenance"
            )
        if optional_string(record.get("campaign_brand_asset_id")) is None:
            raise GenerationInputError(
                "Resolved video source is missing its campaign attachment ID"
            )

    if source.origin == VideoSourceOrigin.user_upload:
        content_validation = metadata_dict(record.get("content_validation"))
        video_track_count = content_validation.get("video_track_count")
        media_data_box_count = content_validation.get("media_data_box_count")
        if (
            optional_string(record.get("sha256")) is None
            or content_validation.get("container") != "mp4"
            or not isinstance(video_track_count, int)
            or isinstance(video_track_count, bool)
            or video_track_count < 1
            or not isinstance(media_data_box_count, int)
            or isinstance(media_data_box_count, bool)
            or media_data_box_count < 1
        ):
            raise GenerationInputError(
                "Uploaded video source is missing verified MP4 content metadata"
            )


def validate_video_submission(
    *,
    video_in: VideoGenerationCreate,
    model: str,
    source: ResolvedVideoSource,
    settings: Settings,
) -> VideoInputMode:
    try:
        validate_resolved_video_source(source)
        validate_video_generation_parameters(
            model=model,
            duration_seconds=video_in.duration_seconds,
            aspect_ratio=video_in.aspect_ratio.value,
            resolution=video_in.resolution.value,
        )
        return validate_video_input_assets(
            model=model,
            input_assets=source.input_assets,
            require_download_url=False,
            settings=settings,
        )
    except GenerationInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


def build_queued_video_models(
    *,
    campaign_id: uuid.UUID,
    video_in: VideoGenerationCreate,
    model: str,
    input_mode: VideoInputMode,
    source: ResolvedVideoSource,
    context_assets: list[dict[str, object]],
    asset_id: uuid.UUID | None = None,
    version_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
) -> tuple[Asset, AssetVersion, GenerationJob]:
    asset_id = asset_id or uuid.uuid4()
    version_id = version_id or uuid.uuid4()
    job_id = job_id or uuid.uuid4()
    generation_parameters: dict[str, object] = {
        "duration": video_in.duration_seconds,
        "aspect_ratio": video_in.aspect_ratio.value,
        "resolution": video_in.resolution.value,
    }
    source_inputs = source.input_assets
    source_resolution = source.as_metadata()
    provenance_inputs = [*source_inputs, *context_assets]
    job_parameters: dict[str, Any] = {
        **generation_parameters,
        "input_mode": input_mode.value,
        "source_origin": source.origin.value,
        "source_version_id": source_resolution["source_version_id"],
        "source_brand_asset_id": source_resolution[
            "source_brand_asset_id"
        ],
        "source_resolution": source_resolution,
        "source_input_assets": source_inputs,
        "context_assets": context_assets,
    }
    job_record = {
        "id": str(job_id),
        "kind": GenerationJobKind.video.value,
        "status": GenerationJobStatus.queued.value,
        "progress_percent": 0,
    }
    provenance = {
        "provider": "gmicloud",
        "model": model,
        "prompt": video_in.prompt,
        "source": "backend_genblaze_video_submission",
        "generation_parameters": generation_parameters,
        "source_resolution": source_resolution,
        "input_assets": provenance_inputs,
        "job": job_record,
    }
    asset = Asset(
        id=asset_id,
        campaign_id=campaign_id,
        title=video_in.title or f"{video_in.channel} video draft",
        format=AssetFormat.video_concept,
        channel=video_in.channel,
        status=video_in.status,
        reviewer=video_in.reviewer,
        tags=list(dict.fromkeys(["genblaze", "video", *video_in.tags])),
        summary=video_in.summary or (
            "Queued Genblaze video generation with durable B2 storage and "
            "provenance metadata."
        ),
    )
    version = AssetVersion(
        id=version_id,
        asset_id=asset_id,
        version_number=1,
        label="Queued Genblaze video",
        prompt=video_in.prompt,
        model=model,
        provider="gmicloud",
        storage_key=build_asset_version_storage_key(
            campaign_id=campaign_id,
            asset_id=asset_id,
            version_number=1,
        ),
        generation_metadata={
            "provider": "gmicloud",
            "model": model,
            "prompt": video_in.prompt,
            "source": "backend_genblaze_video_submission",
            "generation_parameters": generation_parameters,
            "source_resolution": source_resolution,
            "input_assets": provenance_inputs,
            "job": job_record,
            "provenance": provenance,
        },
    )
    version.inputs.extend(
        build_asset_version_input(
            asset_version_id=version_id,
            record=input_record,
        )
        for input_record in provenance_inputs
    )
    job = GenerationJob(
        id=job_id,
        asset_version_id=version_id,
        kind=GenerationJobKind.video.value,
        status=GenerationJobStatus.queued.value,
        provider="gmicloud",
        model=model,
        prompt=video_in.prompt,
        parameters=job_parameters,
        progress_percent=0,
        attempt_count=0,
    )
    asset.versions.append(version)
    version.generation_job = job
    return asset, version, job


def load_video_submission(
    *,
    asset_id: uuid.UUID,
    job_id: uuid.UUID,
    db: Session,
) -> VideoGenerationSubmissionRead:
    asset = db.scalar(
        select(Asset)
        .options(
            selectinload(Asset.versions).selectinload(AssetVersion.inputs)
        )
        .where(Asset.id == asset_id)
    )
    job = db.get(GenerationJob, job_id)
    if asset is None or job is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Queued video generation could not be reloaded",
        )

    return VideoGenerationSubmissionRead(
        asset=AssetRead.model_validate(asset),
        job=GenerationJobRead.model_validate(job),
    )


@router.get(
    "/campaigns/{campaign_id}/generation-jobs",
    response_model=list[GenerationJobRead],
)
def list_campaign_generation_jobs(
    campaign_id: uuid.UUID,
    status_filter: GenerationJobStatus | None = Query(
        default=None,
        alias="status",
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[GenerationJob]:
    get_campaign_or_404(campaign_id, db)
    statement = (
        campaign_generation_job_statement(
            campaign_id=campaign_id,
            status_filter=status_filter,
        )
        .order_by(
            GenerationJob.created_at.desc(),
            GenerationJob.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return list(db.scalars(statement).all())


@router.get(
    "/campaigns/{campaign_id}/generation-jobs/{job_id}",
    response_model=GenerationJobRead,
)
def get_campaign_generation_job(
    campaign_id: uuid.UUID,
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> GenerationJob:
    return get_campaign_generation_job_or_404(
        campaign_id=campaign_id,
        job_id=job_id,
        db=db,
    )


@router.post(
    "/campaigns/{campaign_id}/generation-jobs/{job_id}/cancel",
    response_model=GenerationJobRead,
)
def cancel_campaign_generation_job(
    campaign_id: uuid.UUID,
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> GenerationJob:
    job = get_campaign_generation_job_or_404(
        campaign_id=campaign_id,
        job_id=job_id,
        db=db,
        for_update=True,
    )
    if job.status == GenerationJobStatus.canceled.value:
        db.commit()
        return job

    if job.status != GenerationJobStatus.queued.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only queued generation jobs can be canceled",
        )

    canceled_at = datetime.now(UTC)
    previous_status = job.status
    job.status = GenerationJobStatus.canceled.value
    job.progress_percent = 0
    job.provider_job_id = None
    job.error_message = None
    job.started_at = None
    job.completed_at = canceled_at
    job.asset_version.label = "Canceled Genblaze video"
    sync_generation_job_transition_metadata(
        job=job,
        event="canceled",
        previous_status=previous_status,
        recorded_at=canceled_at,
    )
    db.commit()
    return job


@router.post(
    "/campaigns/{campaign_id}/generation-jobs/{job_id}/retry",
    response_model=GenerationJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_campaign_generation_job(
    campaign_id: uuid.UUID,
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> GenerationJob:
    job = get_campaign_generation_job_or_404(
        campaign_id=campaign_id,
        job_id=job_id,
        db=db,
        for_update=True,
    )
    retryable_statuses = {
        GenerationJobStatus.failed.value,
        GenerationJobStatus.canceled.value,
    }
    if job.status not in retryable_statuses:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only failed or canceled generation jobs can be retried",
        )

    retried_at = datetime.now(UTC)
    previous_status = job.status
    job.status = GenerationJobStatus.queued.value
    job.progress_percent = 0
    job.provider_job_id = None
    job.error_message = None
    job.started_at = None
    job.completed_at = None
    job.asset_version.label = "Queued Genblaze video"
    sync_generation_job_transition_metadata(
        job=job,
        event="retried",
        previous_status=previous_status,
        recorded_at=retried_at,
    )
    db.commit()
    return job


@router.post(
    "/campaigns/{campaign_id}/assets/generate-video",
    response_model=VideoGenerationSubmissionRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_video_generation(
    campaign_id: uuid.UUID,
    video_in: VideoGenerationCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> VideoGenerationSubmissionRead:
    get_campaign_or_404(campaign_id, db)
    model = video_in.model or settings.genblaze_video_model
    source = resolve_video_source(
        campaign_id=campaign_id,
        video_in=video_in,
        db=db,
    )

    input_mode = validate_video_submission(
        video_in=video_in,
        model=model,
        source=source,
        settings=settings,
    )

    context_assets = campaign_brand_context_assets(
        campaign_id=campaign_id,
        db=db,
        exclude_brand_asset_id=source.excluded_context_brand_asset_id,
    )
    asset, _version, job = build_queued_video_models(
        campaign_id=campaign_id,
        video_in=video_in,
        model=model,
        input_mode=input_mode,
        source=source,
        context_assets=context_assets,
    )
    db.add(asset)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Video generation job could not be queued",
        ) from exc

    return load_video_submission(
        asset_id=asset.id,
        job_id=job.id,
        db=db,
    )


@router.post(
    "/campaigns/{campaign_id}/assets/generate-video-with-input",
    response_model=VideoGenerationSubmissionRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_video_generation_with_upload(
    campaign_id: uuid.UUID,
    payload: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    storage: B2StorageService = Depends(get_storage_service),
) -> VideoGenerationSubmissionRead:
    video_in = parse_video_generation_payload(payload)
    if (
        video_in.source_version_id is not None
        or video_in.source_brand_asset_id is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "An uploaded source video cannot be combined with a stored "
                "source version or brand asset"
            ),
        )

    get_campaign_or_404(campaign_id, db)
    filename = normalize_video_upload_filename(file)
    content_type = normalize_video_upload_content_type(
        file=file,
        filename=filename,
    )
    inspection = inspect_video_upload(
        storage=storage,
        file=file,
        max_size_bytes=settings.max_video_source_video_size_bytes,
    )
    content_validation = validate_video_upload_contents(
        file=file,
        inspection=inspection,
    )

    asset_id = uuid.uuid4()
    version_id = uuid.uuid4()
    job_id = uuid.uuid4()
    storage_key = build_asset_version_input_storage_key(
        campaign_id=campaign_id,
        asset_id=asset_id,
        version_number=1,
        role=VIDEO_SOURCE_INPUT_ROLE,
        filename=filename,
    )
    source_input = uploaded_video_input_record(
        storage_key=storage_key,
        filename=filename,
        content_type=content_type,
        inspection=inspection,
        content_validation=content_validation,
    )
    source = resolve_video_source(
        campaign_id=campaign_id,
        video_in=video_in,
        db=db,
        uploaded_input=source_input,
    )
    model = video_in.model or settings.genblaze_video_edit_model
    input_mode = validate_video_submission(
        video_in=video_in,
        model=model,
        source=source,
        settings=settings,
    )
    context_assets = campaign_brand_context_assets(
        campaign_id=campaign_id,
        db=db,
    )
    asset, _version, job = build_queued_video_models(
        campaign_id=campaign_id,
        video_in=video_in,
        model=model,
        input_mode=input_mode,
        source=source,
        context_assets=context_assets,
        asset_id=asset_id,
        version_id=version_id,
        job_id=job_id,
    )

    try:
        storage.upload_fileobj(
            key=storage_key,
            fileobj=file.file,
            content_type=content_type,
            max_size_bytes=settings.max_video_source_video_size_bytes,
            inspection=inspection,
            metadata={
                "campaign_id": str(campaign_id),
                "asset_id": str(asset_id),
                "version_number": 1,
                "content_kind": "asset-version-input",
                "media_kind": "video",
                "role": VIDEO_SOURCE_INPUT_ROLE,
                "filename": filename,
                "sha256": inspection.sha256,
                "container": content_validation.container,
                "major_brand": content_validation.major_brand,
                "video_track_count": content_validation.video_track_count,
            },
        )
    except StorageObjectTooLargeError as exc:
        delete_uploaded_video_safely(
            storage=storage,
            storage_key=storage_key,
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                "Source video must be "
                f"{format_size_limit(settings.max_video_source_video_size_bytes)} "
                "or smaller"
            ),
        ) from exc
    except (
        StorageConfigurationError,
        StorageOperationError,
        BotoCoreError,
        ClientError,
    ) as exc:
        delete_uploaded_video_safely(
            storage=storage,
            storage_key=storage_key,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Source video was not uploaded because B2 storage failed",
        ) from exc

    try:
        db.add(asset)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        delete_uploaded_video_safely(
            storage=storage,
            storage_key=storage_key,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Video generation job could not be queued",
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        delete_uploaded_video_safely(
            storage=storage,
            storage_key=storage_key,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Video generation job metadata could not be saved",
        ) from exc

    return load_video_submission(
        asset_id=asset.id,
        job_id=job.id,
        db=db,
    )
