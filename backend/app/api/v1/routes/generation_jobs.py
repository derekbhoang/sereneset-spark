import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
    infer_asset_media_type,
    optional_string,
    validate_video_generation_parameters,
    validate_video_input_assets,
)
from app.services.storage import build_asset_version_storage_key


router = APIRouter(tags=["generation-jobs"])


def metadata_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def metadata_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []

    return [item for item in value if isinstance(item, dict)]


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
    return {
        "role": "source_creative",
        "storage_key": source_version.artifact_storage_key,
        "filename": filename,
        "content_type": infer_asset_media_type(
            content_type=source_version.artifact_content_type,
            filename=filename,
            url=None,
        ),
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


def build_queued_video_models(
    *,
    campaign_id: uuid.UUID,
    video_in: VideoGenerationCreate,
    model: str,
    input_mode: VideoInputMode,
    source_inputs: list[dict[str, object]],
    context_assets: list[dict[str, object]],
) -> tuple[Asset, AssetVersion, GenerationJob]:
    asset_id = uuid.uuid4()
    version_id = uuid.uuid4()
    job_id = uuid.uuid4()
    generation_parameters: dict[str, object] = {
        "duration": video_in.duration_seconds,
        "aspect_ratio": video_in.aspect_ratio.value,
        "resolution": video_in.resolution.value,
    }
    provenance_inputs = [*source_inputs, *context_assets]
    job_parameters: dict[str, Any] = {
        **generation_parameters,
        "input_mode": input_mode.value,
        "source_version_id": (
            str(video_in.source_version_id)
            if video_in.source_version_id is not None
            else None
        ),
        "source_brand_asset_id": (
            str(video_in.source_brand_asset_id)
            if video_in.source_brand_asset_id is not None
            else None
        ),
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
            "input_assets": provenance_inputs,
            "job": job_record,
            "provenance": provenance,
        },
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
    source_inputs: list[dict[str, object]] = []
    if video_in.source_version_id is not None:
        source_version = get_source_version_or_404(
            campaign_id=campaign_id,
            source_version_id=video_in.source_version_id,
            db=db,
        )
        source_inputs.append(source_version_input_record(source_version))
    elif video_in.source_brand_asset_id is not None:
        source_brand_asset_link = get_source_brand_asset_link_or_404(
            campaign_id=campaign_id,
            source_brand_asset_id=video_in.source_brand_asset_id,
            db=db,
        )
        source_inputs.append(
            campaign_brand_asset_input_record(
                source_brand_asset_link,
                role=VIDEO_SOURCE_INPUT_ROLE,
            )
        )

    try:
        validate_video_generation_parameters(
            model=model,
            duration_seconds=video_in.duration_seconds,
            aspect_ratio=video_in.aspect_ratio.value,
            resolution=video_in.resolution.value,
        )
        input_mode = validate_video_input_assets(
            model=model,
            input_assets=source_inputs,
            require_download_url=False,
            settings=settings,
        )
    except GenerationInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    context_assets = campaign_brand_context_assets(
        campaign_id=campaign_id,
        db=db,
        exclude_brand_asset_id=video_in.source_brand_asset_id,
    )
    asset, _version, job = build_queued_video_models(
        campaign_id=campaign_id,
        video_in=video_in,
        model=model,
        input_mode=input_mode,
        source_inputs=source_inputs,
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
