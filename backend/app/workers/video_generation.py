from __future__ import annotations

import copy
import logging
import mimetypes
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, Protocol

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.models.asset import AssetVersion
from app.models.generation_job import (
    GenerationJob,
    GenerationJobKind,
    GenerationJobStatus,
)
from app.services.generation import (
    GeneratedAsset,
    GenerationConfigurationError,
    GenerationInputError,
    GenerationProviderError,
    GenerationResult,
    GenblazeGenerationService,
    VideoGenerationRequest,
    is_video_asset,
    optional_string,
)
from app.services.storage import (
    B2StorageService,
    StorageConfigurationError,
    StorageOperationError,
    StoredObject,
    build_asset_version_artifact_storage_key,
    normalize_artifact_filename,
    normalize_storage_key,
)


logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]
VIDEO_PROVIDER_PARAMETER_KEYS = ("duration", "aspect_ratio", "resolution")
MAX_ERROR_MESSAGE_LENGTH = 2000


class DownloadUrlSigner(Protocol):
    def generate_presigned_download_url(
        self,
        *,
        key: str,
        expires_seconds: int = 3600,
    ) -> str: ...


class WorkerStorage(DownloadUrlSigner, Protocol):
    def copy_object(
        self,
        *,
        source_key: str,
        destination_key: str,
        content_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        cache_control: str | None = None,
        max_size_bytes: int | None = None,
    ) -> StoredObject: ...

    def upload_json(
        self,
        *,
        key: str,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> StoredObject: ...

    def delete_object(self, *, key: str) -> None: ...


class VideoGenerator(Protocol):
    def generate_video(
        self,
        request: VideoGenerationRequest,
    ) -> GenerationResult: ...


@dataclass(frozen=True)
class VideoJobSnapshot:
    id: uuid.UUID
    asset_version_id: uuid.UUID
    campaign_id: uuid.UUID
    asset_id: uuid.UUID
    version_number: int
    provider: str
    model: str
    prompt: str
    parameters: dict[str, Any]
    attempt_count: int
    started_at: datetime | None
    version_generation_metadata: dict[str, Any]


@dataclass(frozen=True)
class DurableVideoArtifact:
    storage_key: str
    filename: str
    content_type: str
    size_bytes: int | None
    sha256: str | None
    source_storage_key: str | None = None


@dataclass(frozen=True)
class VideoProvenanceContext:
    version_storage_key: str
    campaign: dict[str, Any]
    asset: dict[str, Any]


@dataclass(frozen=True)
class RecoverySummary:
    requeued: int
    failed: int


def utc_now() -> datetime:
    return datetime.now(UTC)


def build_video_job_claim_statement() -> Select[tuple[GenerationJob]]:
    return (
        select(GenerationJob)
        .where(
            GenerationJob.kind == GenerationJobKind.video.value,
            GenerationJob.status == GenerationJobStatus.queued.value,
        )
        .order_by(GenerationJob.created_at.asc(), GenerationJob.id.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def job_metadata_record(job: GenerationJob) -> dict[str, object | None]:
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


def update_version_job_metadata(job: GenerationJob) -> None:
    version = job.asset_version
    metadata = dict(version.generation_metadata or {})
    job_record = job_metadata_record(job)
    metadata["job"] = job_record

    provenance = metadata.get("provenance")
    if isinstance(provenance, dict):
        metadata["provenance"] = {**provenance, "job": job_record}

    if job.error_message:
        metadata["failure"] = {
            "message": job.error_message,
            "recorded_at": (
                job.completed_at or job.updated_at or utc_now()
            ).isoformat(),
        }
    else:
        metadata.pop("failure", None)

    version.generation_metadata = metadata


def claim_next_video_job(
    db: Session,
    *,
    now: datetime | None = None,
) -> uuid.UUID | None:
    claimed_at = now or utc_now()

    try:
        job = db.scalar(build_video_job_claim_statement())
        if job is None:
            db.rollback()
            return None

        job_id = job.id
        job.status = GenerationJobStatus.running.value
        job.progress_percent = 5
        job.provider_job_id = None
        job.attempt_count = (job.attempt_count or 0) + 1
        job.error_message = None
        job.started_at = claimed_at
        job.completed_at = None
        update_version_job_metadata(job)
        db.commit()
        return job_id
    except Exception:
        db.rollback()
        raise


def recover_stale_video_jobs(
    db: Session,
    *,
    stale_after_seconds: int,
    max_attempts: int,
    now: datetime | None = None,
) -> RecoverySummary:
    recovered_at = now or utc_now()
    stale_before = recovered_at - timedelta(seconds=stale_after_seconds)
    statement = (
        select(GenerationJob)
        .where(
            GenerationJob.kind == GenerationJobKind.video.value,
            GenerationJob.status == GenerationJobStatus.running.value,
            or_(
                GenerationJob.started_at.is_(None),
                GenerationJob.started_at < stale_before,
            ),
        )
        .order_by(GenerationJob.created_at.asc(), GenerationJob.id.asc())
        .with_for_update(skip_locked=True)
    )

    try:
        jobs = list(db.scalars(statement).all())
        requeued = 0
        failed = 0

        for job in jobs:
            job.provider_job_id = None
            if job.attempt_count >= max_attempts:
                job.status = GenerationJobStatus.failed.value
                job.error_message = (
                    "Generation worker stopped before the job completed"
                )
                job.completed_at = recovered_at
                failed += 1
            else:
                job.status = GenerationJobStatus.queued.value
                job.progress_percent = 0
                job.error_message = None
                job.started_at = None
                job.completed_at = None
                requeued += 1

            update_version_job_metadata(job)

        if jobs:
            db.commit()
        else:
            db.rollback()

        return RecoverySummary(requeued=requeued, failed=failed)
    except Exception:
        db.rollback()
        raise


def load_running_video_job(
    db: Session,
    job_id: uuid.UUID,
) -> VideoJobSnapshot | None:
    job = db.get(GenerationJob, job_id)
    if job is None or job.status != GenerationJobStatus.running.value:
        return None

    version = job.asset_version
    asset = version.asset
    return VideoJobSnapshot(
        id=job.id,
        asset_version_id=job.asset_version_id,
        campaign_id=asset.campaign_id,
        asset_id=asset.id,
        version_number=version.version_number,
        provider=job.provider,
        model=job.model,
        prompt=job.prompt,
        parameters=copy.deepcopy(job.parameters or {}),
        attempt_count=job.attempt_count,
        started_at=job.started_at,
        version_generation_metadata=copy.deepcopy(
            version.generation_metadata or {}
        ),
    )


def public_enum_value(value: object) -> object:
    return getattr(value, "value", value)


def load_video_provenance_context(
    db: Session,
    job_id: uuid.UUID,
) -> VideoProvenanceContext | None:
    job = db.get(GenerationJob, job_id)
    if job is None or job.status != GenerationJobStatus.running.value:
        return None

    version = job.asset_version
    asset = version.asset
    campaign = asset.campaign
    return VideoProvenanceContext(
        version_storage_key=version.storage_key,
        campaign={
            "id": str(campaign.id),
            "name": campaign.name,
            "product": campaign.product,
            "audience": campaign.audience,
            "status": campaign.status,
            "channels": list(campaign.channels),
            "brand_inputs": list(campaign.brand_inputs),
        },
        asset={
            "id": str(asset.id),
            "title": asset.title,
            "format": public_enum_value(asset.format),
            "channel": asset.channel,
            "status": public_enum_value(asset.status),
            "reviewer": asset.reviewer,
            "tags": list(asset.tags),
            "summary": asset.summary,
        },
    )


def job_asset_records(
    parameters: dict[str, Any],
    key: str,
) -> list[dict[str, Any]]:
    value = parameters.get(key, [])
    if value is None:
        return []

    if not isinstance(value, list) or any(
        not isinstance(item, dict) for item in value
    ):
        raise GenerationInputError(f"Generation job '{key}' is malformed")

    return [copy.deepcopy(item) for item in value]


def prepare_source_input_assets(
    *,
    source_inputs: list[dict[str, Any]],
    storage: DownloadUrlSigner,
    expires_seconds: int,
) -> list[dict[str, Any]]:
    prepared_inputs: list[dict[str, Any]] = []

    for input_asset in source_inputs:
        storage_key = optional_string(input_asset.get("storage_key"))
        if storage_key is None:
            raise GenerationInputError(
                "Video source input is missing its B2 storage key"
            )

        prepared_inputs.append(
            {
                **input_asset,
                "url": storage.generate_presigned_download_url(
                    key=storage_key,
                    expires_seconds=expires_seconds,
                ),
            }
        )

    return prepared_inputs


def video_provider_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        key: parameters[key]
        for key in VIDEO_PROVIDER_PARAMETER_KEYS
        if parameters.get(key) is not None
    }


def video_content_type(asset: GeneratedAsset, filename: str) -> str:
    content_type = (asset.content_type or "").split(";", maxsplit=1)[0].strip()
    if content_type.lower().startswith("video/"):
        return content_type.lower()

    guessed_content_type, _encoding = mimetypes.guess_type(filename)
    if guessed_content_type and guessed_content_type.startswith("video/"):
        return guessed_content_type

    return "video/mp4"


def select_durable_video_artifact(
    result: GenerationResult,
    *,
    max_size_bytes: int,
) -> DurableVideoArtifact:
    artifact = next(
        (
            asset
            for asset in result.assets
            if asset.storage_key and is_video_asset(asset)
        ),
        None,
    )
    if artifact is None or artifact.storage_key is None:
        raise GenerationProviderError(
            "Genblaze did not return a durable B2 video artifact"
        )

    storage_key = normalize_storage_key(artifact.storage_key)
    fallback_filename = PurePosixPath(storage_key).name or "generated-video.mp4"
    try:
        filename = normalize_artifact_filename(
            artifact.filename or fallback_filename
        )
    except ValueError:
        filename = "generated-video.mp4"

    size_bytes = artifact.size_bytes
    if size_bytes is not None and size_bytes <= 0:
        raise GenerationProviderError("Generated video artifact was empty")

    if size_bytes is not None and size_bytes > max_size_bytes:
        raise GenerationProviderError(
            "Generated video artifact exceeds the configured size limit"
        )

    return DurableVideoArtifact(
        storage_key=storage_key,
        filename=filename,
        content_type=video_content_type(artifact, filename),
        size_bytes=size_bytes,
        sha256=artifact.sha256,
        source_storage_key=storage_key,
    )


def store_video_artifact(
    *,
    snapshot: VideoJobSnapshot,
    artifact: DurableVideoArtifact,
    storage: WorkerStorage,
    max_size_bytes: int,
) -> DurableVideoArtifact:
    destination_key = build_asset_version_artifact_storage_key(
        campaign_id=snapshot.campaign_id,
        asset_id=snapshot.asset_id,
        version_number=snapshot.version_number,
        filename=artifact.filename,
    )
    source_storage_key = artifact.source_storage_key or artifact.storage_key
    stored_object = storage.copy_object(
        source_key=source_storage_key,
        destination_key=destination_key,
        content_type=artifact.content_type,
        metadata={
            "campaign_id": str(snapshot.campaign_id),
            "asset_id": str(snapshot.asset_id),
            "version_id": str(snapshot.asset_version_id),
            "version_number": snapshot.version_number,
            "generation_job_id": str(snapshot.id),
            "content_kind": "asset-version-artifact",
            "filename": artifact.filename,
            "source": "genblaze",
            "source_storage_key": source_storage_key,
            "source_sha256": artifact.sha256,
        },
        max_size_bytes=max_size_bytes,
    )

    return DurableVideoArtifact(
        storage_key=stored_object.key,
        filename=artifact.filename,
        content_type=stored_object.content_type,
        size_bytes=stored_object.size,
        sha256=artifact.sha256,
        source_storage_key=source_storage_key,
    )


def generated_asset_metadata(asset: GeneratedAsset) -> dict[str, object | None]:
    return {
        "url": asset.url,
        "storage_key": asset.storage_key,
        "sha256": asset.sha256,
        "content_type": asset.content_type,
        "size_bytes": asset.size_bytes,
        "filename": asset.filename,
    }


def build_completed_generation_metadata(
    *,
    snapshot: VideoJobSnapshot,
    result: GenerationResult,
    artifact: DurableVideoArtifact,
    completed_at: datetime,
    sidecar_storage_key: str | None = None,
) -> dict[str, Any]:
    source_inputs = job_asset_records(snapshot.parameters, "source_input_assets")
    context_assets = job_asset_records(snapshot.parameters, "context_assets")
    input_assets = [*source_inputs, *context_assets]
    generation_parameters = video_provider_parameters(snapshot.parameters)
    asset_records = [generated_asset_metadata(asset) for asset in result.assets]
    job_record: dict[str, object | None] = {
        "id": str(snapshot.id),
        "kind": GenerationJobKind.video.value,
        "status": GenerationJobStatus.succeeded.value,
        "progress_percent": 100,
        "provider_job_id": result.provider_job_id,
        "attempt_count": snapshot.attempt_count,
        "error_message": None,
        "started_at": (
            snapshot.started_at.isoformat() if snapshot.started_at else None
        ),
        "completed_at": completed_at.isoformat(),
    }
    artifact_flow: dict[str, object | None] = {
        "storage_key": artifact.storage_key,
        "filename": artifact.filename,
        "content_type": artifact.content_type,
        "size_bytes": artifact.size_bytes,
        "source": "genblaze_b2_server_side_copy",
        "storage_strategy": "server_side_copy",
        "source_storage_key": (
            artifact.source_storage_key or artifact.storage_key
        ),
        "sha256": artifact.sha256,
        "source_sha256": artifact.sha256,
    }
    sidecar_record = {
        "storage_key": sidecar_storage_key,
        "content_type": "application/json",
    }
    submission_provenance = snapshot.version_generation_metadata.get(
        "provenance"
    )
    provenance: dict[str, Any] = {
        "schema_version": 1,
        "provider": result.provider,
        "model": result.model,
        "prompt": result.prompt,
        "source": "backend_genblaze_video_worker",
        "based_on_version_id": snapshot.parameters.get("source_version_id"),
        "input_mode": snapshot.parameters.get("input_mode"),
        "generation_parameters": generation_parameters,
        "manifest_uri": result.manifest_uri,
        "manifest_hash": result.manifest_hash,
        "manifest_verified": result.manifest_verified,
        "provider_job_id": result.provider_job_id,
        "input_assets": input_assets,
        "assets": asset_records,
        "artifact_flow": artifact_flow,
        "sidecar": sidecar_record,
        "job": job_record,
        "recorded_at": completed_at.isoformat(),
    }
    if isinstance(submission_provenance, dict):
        provenance["submission_provenance"] = submission_provenance

    return {
        **snapshot.version_generation_metadata,
        **copy.deepcopy(result.generation_metadata),
        "provenance_schema_version": 1,
        "provider": result.provider,
        "model": result.model,
        "prompt": result.prompt,
        "source": "backend_genblaze_video_worker",
        "based_on_version_id": snapshot.parameters.get("source_version_id"),
        "input_mode": snapshot.parameters.get("input_mode"),
        "generation_parameters": generation_parameters,
        "manifest_uri": result.manifest_uri,
        "manifest_hash": result.manifest_hash,
        "manifest_verified": result.manifest_verified,
        "provider_job_id": result.provider_job_id,
        "input_assets": input_assets,
        "assets": asset_records,
        "artifact_flow": artifact_flow,
        "sidecar": sidecar_record,
        "job": job_record,
        "provenance": provenance,
    }


def build_video_provenance_sidecar(
    *,
    context: VideoProvenanceContext,
    snapshot: VideoJobSnapshot,
    result: GenerationResult,
    artifact: DurableVideoArtifact,
    generation_metadata: dict[str, Any],
    stored_at: datetime,
) -> dict[str, object]:
    input_assets = generation_metadata.get("input_assets")
    if not isinstance(input_assets, list):
        input_assets = []

    return {
        "campaign": copy.deepcopy(context.campaign),
        "asset": copy.deepcopy(context.asset),
        "version": {
            "id": str(snapshot.asset_version_id),
            "version_number": snapshot.version_number,
            "label": f"Genblaze video {snapshot.version_number}",
            "prompt": result.prompt,
            "model": result.model,
            "provider": result.provider,
            "storage_key": context.version_storage_key,
            "artifact_storage_key": artifact.storage_key,
            "artifact_filename": artifact.filename,
            "artifact_content_type": artifact.content_type,
            "artifact_size_bytes": artifact.size_bytes,
            "input_assets": copy.deepcopy(input_assets),
            "generation_metadata": copy.deepcopy(generation_metadata),
        },
        "stored_at": stored_at.isoformat(),
    }


def upload_video_provenance_sidecar(
    *,
    storage: WorkerStorage,
    context: VideoProvenanceContext,
    snapshot: VideoJobSnapshot,
    result: GenerationResult,
    artifact: DurableVideoArtifact,
    generation_metadata: dict[str, Any],
    stored_at: datetime,
) -> StoredObject:
    expected_storage_key = normalize_storage_key(context.version_storage_key)
    stored_object = storage.upload_json(
        key=expected_storage_key,
        data=build_video_provenance_sidecar(
            context=context,
            snapshot=snapshot,
            result=result,
            artifact=artifact,
            generation_metadata=generation_metadata,
            stored_at=stored_at,
        ),
        metadata={
            "campaign_id": str(snapshot.campaign_id),
            "asset_id": str(snapshot.asset_id),
            "version_id": str(snapshot.asset_version_id),
            "version_number": snapshot.version_number,
            "generation_job_id": str(snapshot.id),
            "provider": result.provider,
            "model": result.model,
            "manifest_hash": result.manifest_hash,
            "content_kind": "asset-version-sidecar",
        },
    )
    if stored_object.key != expected_storage_key:
        raise StorageOperationError(
            "B2 stored the provenance sidecar at an unexpected key"
        )

    return stored_object


def cleanup_video_outputs(
    *,
    storage: WorkerStorage,
    storage_keys: list[str | None],
    protected_storage_keys: list[str | None] | None = None,
) -> None:
    protected_keys = {
        normalize_storage_key(storage_key)
        for storage_key in (protected_storage_keys or [])
        if storage_key is not None
    }
    deleted_keys: set[str] = set()
    for raw_storage_key in storage_keys:
        if raw_storage_key is None:
            continue

        storage_key = normalize_storage_key(raw_storage_key)
        if storage_key in deleted_keys or storage_key in protected_keys:
            continue

        try:
            storage.delete_object(key=storage_key)
            deleted_keys.add(storage_key)
        except Exception:
            logger.exception(
                "Could not clean up incomplete video output %s",
                storage_key,
            )


def finalize_video_job_success(
    db: Session,
    *,
    snapshot: VideoJobSnapshot,
    result: GenerationResult,
    artifact: DurableVideoArtifact,
    generation_metadata: dict[str, Any] | None = None,
    sidecar_storage_key: str | None = None,
    completed_at: datetime | None = None,
) -> bool:
    finished_at = completed_at or utc_now()
    statement = (
        select(GenerationJob)
        .where(GenerationJob.id == snapshot.id)
        .with_for_update()
    )

    try:
        job = db.scalar(statement)
        if job is None or job.status != GenerationJobStatus.running.value:
            db.rollback()
            return False

        version = job.asset_version
        version.label = f"Genblaze video {version.version_number}"
        version.provider = result.provider
        version.model = result.model
        version.prompt = result.prompt
        version.artifact_storage_key = artifact.storage_key
        version.artifact_filename = artifact.filename
        version.artifact_content_type = artifact.content_type
        version.artifact_size_bytes = artifact.size_bytes
        if sidecar_storage_key is not None:
            version.storage_key = normalize_storage_key(sidecar_storage_key)
        version.generation_metadata = copy.deepcopy(
            generation_metadata
            if generation_metadata is not None
            else build_completed_generation_metadata(
                snapshot=snapshot,
                result=result,
                artifact=artifact,
                completed_at=finished_at,
                sidecar_storage_key=sidecar_storage_key,
            )
        )
        version.asset.updated_at = finished_at

        job.status = GenerationJobStatus.succeeded.value
        job.provider = result.provider
        job.model = result.model
        job.prompt = result.prompt
        job.progress_percent = 100
        job.provider_job_id = result.provider_job_id
        job.error_message = None
        job.completed_at = finished_at
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


def safe_worker_error_message(exc: Exception) -> str:
    expected_errors = (
        GenerationConfigurationError,
        GenerationInputError,
        GenerationProviderError,
        StorageConfigurationError,
        StorageOperationError,
        ValueError,
    )
    if isinstance(exc, expected_errors):
        message = str(exc).strip() or type(exc).__name__
    else:
        message = "Unexpected video generation worker error"

    return message[:MAX_ERROR_MESSAGE_LENGTH]


def mark_video_job_failed(
    db: Session,
    *,
    job_id: uuid.UUID,
    error_message: str,
    completed_at: datetime | None = None,
) -> bool:
    failed_at = completed_at or utc_now()
    statement = (
        select(GenerationJob)
        .where(GenerationJob.id == job_id)
        .with_for_update()
    )

    try:
        job = db.scalar(statement)
        if job is None or job.status != GenerationJobStatus.running.value:
            db.rollback()
            return False

        job.status = GenerationJobStatus.failed.value
        job.error_message = error_message[:MAX_ERROR_MESSAGE_LENGTH]
        job.completed_at = failed_at
        update_version_job_metadata(job)
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


def execute_video_job(
    job_id: uuid.UUID,
    *,
    session_factory: SessionFactory,
    storage: WorkerStorage,
    generation: VideoGenerator,
    settings: Settings,
) -> bool:
    with session_factory() as db:
        snapshot = load_running_video_job(db, job_id)

    if snapshot is None:
        return False

    source_inputs = job_asset_records(
        snapshot.parameters,
        "source_input_assets",
    )
    context_assets = job_asset_records(snapshot.parameters, "context_assets")
    presigned_url_ttl = max(
        3600,
        settings.genblaze_video_timeout_seconds + 300,
    )
    prepared_source_inputs = prepare_source_input_assets(
        source_inputs=source_inputs,
        storage=storage,
        expires_seconds=presigned_url_ttl,
    )
    result = generation.generate_video(
        VideoGenerationRequest(
            prompt=snapshot.prompt,
            model=snapshot.model,
            timeout_seconds=settings.genblaze_video_timeout_seconds,
            parameters=video_provider_parameters(snapshot.parameters),
            input_assets=prepared_source_inputs,
            context_assets=context_assets,
        )
    )
    generated_artifact = select_durable_video_artifact(
        result,
        max_size_bytes=settings.max_generated_video_size_bytes,
    )
    artifact = store_video_artifact(
        snapshot=snapshot,
        artifact=generated_artifact,
        storage=storage,
        max_size_bytes=settings.max_generated_video_size_bytes,
    )

    with session_factory() as db:
        provenance_context = load_video_provenance_context(db, job_id)

    if provenance_context is None:
        cleanup_video_outputs(
            storage=storage,
            storage_keys=[artifact.storage_key],
            protected_storage_keys=[artifact.source_storage_key],
        )
        return False

    completed_at = utc_now()
    generation_metadata = build_completed_generation_metadata(
        snapshot=snapshot,
        result=result,
        artifact=artifact,
        completed_at=completed_at,
        sidecar_storage_key=provenance_context.version_storage_key,
    )

    try:
        sidecar = upload_video_provenance_sidecar(
            storage=storage,
            context=provenance_context,
            snapshot=snapshot,
            result=result,
            artifact=artifact,
            generation_metadata=generation_metadata,
            stored_at=completed_at,
        )
        with session_factory() as db:
            finalized = finalize_video_job_success(
                db,
                snapshot=snapshot,
                result=result,
                artifact=artifact,
                generation_metadata=generation_metadata,
                sidecar_storage_key=sidecar.key,
                completed_at=completed_at,
            )
    except Exception:
        cleanup_video_outputs(
            storage=storage,
            storage_keys=[
                artifact.storage_key,
                provenance_context.version_storage_key,
            ],
            protected_storage_keys=[artifact.source_storage_key],
        )
        raise

    if not finalized:
        cleanup_video_outputs(
            storage=storage,
            storage_keys=[artifact.storage_key, sidecar.key],
            protected_storage_keys=[artifact.source_storage_key],
        )

    return finalized


def run_worker_once(
    *,
    session_factory: SessionFactory = SessionLocal,
    storage: WorkerStorage | None = None,
    generation: VideoGenerator | None = None,
    settings: Settings | None = None,
) -> bool:
    worker_settings = settings or get_settings()
    with session_factory() as db:
        job_id = claim_next_video_job(db)

    if job_id is None:
        return False

    storage_service = storage or B2StorageService(worker_settings)
    generation_service = generation or GenblazeGenerationService(worker_settings)

    try:
        execute_video_job(
            job_id,
            session_factory=session_factory,
            storage=storage_service,
            generation=generation_service,
            settings=worker_settings,
        )
    except Exception as exc:
        logger.exception("Video generation job %s failed", job_id)
        error_message = safe_worker_error_message(exc)
        with session_factory() as db:
            mark_video_job_failed(
                db,
                job_id=job_id,
                error_message=error_message,
            )

    return True


def run_worker_forever(
    *,
    session_factory: SessionFactory = SessionLocal,
    settings: Settings | None = None,
) -> None:
    worker_settings = settings or get_settings()
    logger.info("Video generation worker starting")

    try:
        with session_factory() as db:
            recovery = recover_stale_video_jobs(
                db,
                stale_after_seconds=(
                    worker_settings.generation_job_stale_after_seconds
                ),
                max_attempts=worker_settings.generation_job_max_attempts,
            )
        if recovery.requeued or recovery.failed:
            logger.warning(
                "Recovered stale video jobs: requeued=%s failed=%s",
                recovery.requeued,
                recovery.failed,
            )
    except Exception:
        logger.exception("Could not recover stale video generation jobs")

    while True:
        try:
            handled_job = run_worker_once(
                session_factory=session_factory,
                settings=worker_settings,
            )
        except Exception:
            logger.exception("Video generation worker iteration failed")
            handled_job = False

        if not handled_job:
            time.sleep(worker_settings.generation_worker_poll_seconds)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        run_worker_forever()
    except KeyboardInterrupt:
        logger.info("Video generation worker stopped")


if __name__ == "__main__":
    main()
