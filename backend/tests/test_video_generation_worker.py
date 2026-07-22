import json
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from threading import Event
from unittest.mock import MagicMock, patch

from sqlalchemy.dialects import postgresql

from app.core.config import Settings
from app.models.asset import Asset, AssetFormat, AssetVersion, ReviewStatus
from app.models.generation_job import GenerationJob, GenerationJobStatus
from app.services.generation import (
    GeneratedAsset,
    GenerationInputError,
    GenerationProviderError,
    GenerationResult,
)
from app.services.storage import StoredObject
from app.workers.video_generation import (
    DurableVideoArtifact,
    RecoverySummary,
    VideoJobSnapshot,
    VideoProvenanceContext,
    build_completed_generation_metadata,
    build_video_provenance_sidecar,
    build_video_job_claim_statement,
    claim_next_video_job,
    cleanup_video_outputs,
    finalize_video_job_success,
    mark_video_job_failed,
    prepare_source_input_assets,
    prepare_video_generation_request,
    recover_stale_video_jobs,
    run_worker_forever,
    safe_worker_error_message,
    select_durable_video_artifact,
    store_video_artifact,
    upload_video_provenance_sidecar,
    validate_video_execution_provenance,
    video_provider_parameters,
)


def make_asset_version() -> tuple[Asset, AssetVersion]:
    asset = Asset(
        id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        title="Launch video",
        format=AssetFormat.video_concept,
        channel="Paid social",
        status=ReviewStatus.draft,
        reviewer=None,
        tags=["video"],
        summary="Video draft",
    )
    version = AssetVersion(
        id=uuid.uuid4(),
        asset_id=asset.id,
        version_number=1,
        label="Queued Genblaze video",
        prompt="Orbit around the product.",
        model="Veo3-Fast",
        provider="gmicloud",
        storage_key="campaigns/test/metadata.json",
        generation_metadata={
            "source": "backend_genblaze_video_submission",
            "provenance": {"source": "backend_genblaze_video_submission"},
        },
    )
    version.asset = asset
    return asset, version


def make_job(
    *,
    status: str = GenerationJobStatus.queued.value,
    attempt_count: int = 0,
) -> GenerationJob:
    _asset, version = make_asset_version()
    job = GenerationJob(
        id=uuid.uuid4(),
        asset_version_id=version.id,
        kind="video",
        status=status,
        provider="gmicloud",
        model="Veo3-Fast",
        prompt="Orbit around the product.",
        parameters={
            "duration": 4,
            "aspect_ratio": "16:9",
            "resolution": "720p",
            "input_mode": "text_to_video",
            "source_input_assets": [],
            "context_assets": [],
        },
        progress_percent=0,
        attempt_count=attempt_count,
    )
    job.asset_version = version
    return job


def make_result(
    *,
    model: str = "Veo3-Fast",
    prompt: str = "Orbit around the product.",
    input_mode: str | None = None,
    input_assets_parameter: str | None = None,
    provider_source_parameter: str | None = None,
    extra_metadata: dict[str, object] | None = None,
    manifest_uri: str = "b2://bucket/run/manifest.json",
) -> GenerationResult:
    genblaze_metadata: dict[str, object] = {
        "modality": "video",
        "asset_count": 1,
    }
    if input_mode is not None:
        genblaze_metadata["input_mode"] = input_mode
    if input_assets_parameter is not None:
        genblaze_metadata["input_assets_parameter"] = input_assets_parameter
    if provider_source_parameter is not None:
        genblaze_metadata["provider_source_parameter"] = provider_source_parameter

    return GenerationResult(
        provider="gmicloud",
        model=model,
        prompt=prompt,
        manifest_uri=manifest_uri,
        manifest_hash="manifest-hash",
        manifest_verified=True,
        provider_job_id="provider-job-123",
        assets=[
            GeneratedAsset(
                url="https://example.com/generated.mp4",
                storage_key="sereneset-spark/genblaze/run/generated.mp4",
                sha256="a" * 64,
                content_type="video/mp4",
                size_bytes=4096,
                filename="generated.mp4",
            )
        ],
        generation_metadata={
            "genblaze": genblaze_metadata,
            **(extra_metadata or {}),
        },
    )


def make_video_edit_snapshot(
    *,
    input_mode: str = "video_to_video",
) -> VideoJobSnapshot:
    return VideoJobSnapshot(
        id=uuid.uuid4(),
        asset_version_id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        version_number=1,
        provider="gmicloud",
        model="wan2.7-videoedit",
        prompt="Make the background move gently.",
        parameters={
            "duration": 4,
            "aspect_ratio": "16:9",
            "resolution": "720p",
            "input_mode": input_mode,
            "source_origin": "user_upload",
            "source_resolution": {
                "origin": "user_upload",
                "source_version_id": None,
                "source_brand_asset_id": None,
            },
            "source_input_assets": [
                {
                    "role": "source_creative",
                    "storage_key": "campaigns/source/source.mp4",
                    "filename": "source.mp4",
                    "content_type": "video/mp4",
                    "media_kind": "video",
                    "size_bytes": 4096,
                    "sha256": "b" * 64,
                    "source": "user_upload",
                    "storage_ownership": "asset_version",
                    "content_validation": {
                        "container": "mp4",
                        "video_track_count": 1,
                        "media_data_box_count": 1,
                    },
                }
            ],
            "context_assets": [
                {
                    "role": "brand_reference",
                    "storage_key": "brand-assets/guidelines.pdf",
                    "filename": "guidelines.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": 1024,
                    "source": "campaign_brand_asset",
                }
            ],
        },
        attempt_count=1,
        started_at=datetime(2026, 7, 22, 1, 0, tzinfo=UTC),
        version_generation_metadata={"source": "backend_genblaze_video_submission"},
    )


def make_video_refinement_snapshot() -> VideoJobSnapshot:
    asset_id = uuid.uuid4()
    source_version_id = uuid.uuid4()
    operation = "video_refinement"
    return VideoJobSnapshot(
        id=uuid.uuid4(),
        asset_version_id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        asset_id=asset_id,
        version_number=4,
        provider="gmicloud",
        model="wan2.7-videoedit",
        prompt="Keep the product fixed and move only the background.",
        parameters={
            "operation": operation,
            "input_mode": "video_to_video",
            "source_origin": "asset_version",
            "source_version_id": str(source_version_id),
            "source_resolution": {
                "origin": "asset_version",
                "source_version_id": str(source_version_id),
                "source_brand_asset_id": None,
            },
            "source_input_assets": [
                {
                    "role": "source_creative",
                    "storage_key": "campaigns/source/source.mp4",
                    "filename": "source.mp4",
                    "content_type": "video/mp4",
                    "media_kind": "video",
                    "size_bytes": 4096,
                    "sha256": "b" * 64,
                    "source": "source_version_artifact",
                    "storage_ownership": "source_asset_version",
                    "source_asset_id": str(asset_id),
                    "source_version_id": str(source_version_id),
                    "source_version_number": 3,
                }
            ],
            "context_assets": [],
        },
        attempt_count=1,
        started_at=datetime(2026, 7, 22, 3, 0, tzinfo=UTC),
        version_generation_metadata={
            "operation": operation,
            "based_on_version_id": str(source_version_id),
            "provenance": {
                "operation": operation,
                "based_on_version_id": str(source_version_id),
                "request": {"operation": operation},
            },
        },
    )


class VideoGenerationWorkerTests(unittest.TestCase):
    def test_worker_stops_claiming_jobs_after_shutdown_is_requested(self) -> None:
        stop_event = Event()
        settings = MagicMock(
            generation_job_stale_after_seconds=1800,
            generation_job_max_attempts=2,
            generation_worker_poll_seconds=2,
        )
        session_factory = MagicMock()

        def run_current_job(**_kwargs: object) -> bool:
            stop_event.set()
            return True

        with (
            patch(
                "app.workers.video_generation.recover_stale_video_jobs",
                return_value=RecoverySummary(requeued=0, failed=0),
            ),
            patch(
                "app.workers.video_generation.run_worker_once",
                side_effect=run_current_job,
            ) as run_once,
        ):
            run_worker_forever(
                session_factory=session_factory,
                settings=settings,
                stop_event=stop_event,
            )

        run_once.assert_called_once_with(
            session_factory=session_factory,
            settings=settings,
        )

    def test_claim_statement_uses_postgresql_skip_locked(self) -> None:
        compiled = str(
            build_video_job_claim_statement().compile(dialect=postgresql.dialect())
        )

        self.assertIn("FOR UPDATE SKIP LOCKED", compiled)
        self.assertIn("ORDER BY generation_jobs.created_at ASC", compiled)
        self.assertIn("LIMIT", compiled)

    def test_claim_marks_job_running_and_commits(self) -> None:
        claimed_at = datetime(2026, 7, 15, 1, 2, tzinfo=UTC)
        job = make_job()
        db = MagicMock()
        db.scalar.return_value = job

        job_id = claim_next_video_job(db, now=claimed_at)

        self.assertEqual(job_id, job.id)
        self.assertEqual(job.status, GenerationJobStatus.running.value)
        self.assertEqual(job.progress_percent, 5)
        self.assertEqual(job.attempt_count, 1)
        self.assertEqual(job.started_at, claimed_at)
        self.assertEqual(
            job.asset_version.generation_metadata["job"]["status"],
            GenerationJobStatus.running.value,
        )
        db.commit.assert_called_once_with()

    def test_empty_claim_rolls_back_read_transaction(self) -> None:
        db = MagicMock()
        db.scalar.return_value = None

        self.assertIsNone(claim_next_video_job(db))

        db.rollback.assert_called_once_with()
        db.commit.assert_not_called()

    def test_recovery_requeues_once_then_fails_at_attempt_limit(self) -> None:
        now = datetime(2026, 7, 15, 2, 0, tzinfo=UTC)
        requeue_job = make_job(
            status=GenerationJobStatus.running.value,
            attempt_count=1,
        )
        requeue_job.started_at = now - timedelta(hours=1)
        fail_job = make_job(
            status=GenerationJobStatus.running.value,
            attempt_count=2,
        )
        fail_job.started_at = now - timedelta(hours=1)
        scalar_result = MagicMock()
        scalar_result.all.return_value = [requeue_job, fail_job]
        db = MagicMock()
        db.scalars.return_value = scalar_result

        summary = recover_stale_video_jobs(
            db,
            stale_after_seconds=1800,
            max_attempts=2,
            now=now,
        )

        self.assertEqual(summary.requeued, 1)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(requeue_job.status, GenerationJobStatus.queued.value)
        self.assertIsNone(requeue_job.started_at)
        self.assertEqual(fail_job.status, GenerationJobStatus.failed.value)
        self.assertEqual(fail_job.completed_at, now)
        db.commit.assert_called_once_with()

    def test_prepares_temporary_b2_url_without_mutating_provenance(self) -> None:
        source = {
            "role": "source_creative",
            "storage_key": "campaigns/source/product.jpg",
            "filename": "product.jpg",
            "content_type": "image/jpeg",
            "size_bytes": 2048,
        }
        storage = MagicMock()
        storage.generate_presigned_download_url.return_value = (
            "https://s3.example.com/product.jpg?signature=temporary"
        )

        prepared = prepare_source_input_assets(
            source_inputs=[source],
            storage=storage,
            expires_seconds=3600,
        )

        self.assertNotIn("url", source)
        self.assertIn("signature=temporary", prepared[0]["url"])
        storage.generate_presigned_download_url.assert_called_once_with(
            key=source["storage_key"],
            expires_seconds=3600,
        )

    def test_prepares_video_edit_request_from_validated_stored_source(self) -> None:
        snapshot = make_video_edit_snapshot()
        storage = MagicMock()
        storage.generate_presigned_download_url.return_value = (
            "https://s3.example.com/source.mp4?signature=temporary"
        )
        settings = Settings(
            _env_file=None,
            GENBLAZE_VIDEO_TO_VIDEO_ENABLED=True,
            GENBLAZE_VIDEO_TIMEOUT_SECONDS=4000,
        )

        request = prepare_video_generation_request(
            snapshot=snapshot,
            storage=storage,
            settings=settings,
        )

        self.assertEqual(request.model, "wan2.7-videoedit")
        self.assertEqual(request.timeout_seconds, 4000)
        self.assertEqual(request.parameters, {})
        self.assertEqual(len(request.input_assets), 1)
        self.assertIn("signature=temporary", request.input_assets[0]["url"])
        self.assertEqual(
            request.context_assets,
            snapshot.parameters["context_assets"],
        )
        self.assertNotIn(
            "url",
            snapshot.parameters["source_input_assets"][0],
        )
        storage.generate_presigned_download_url.assert_called_once_with(
            key="campaigns/source/source.mp4",
            expires_seconds=4300,
        )

    def test_rejects_worker_input_mode_mismatch_before_signing(self) -> None:
        snapshot = make_video_edit_snapshot(input_mode="image_to_video")
        storage = MagicMock()
        settings = Settings(
            _env_file=None,
            GENBLAZE_VIDEO_TO_VIDEO_ENABLED=True,
        )

        with self.assertRaisesRegex(
            GenerationInputError,
            "input mode does not match",
        ):
            prepare_video_generation_request(
                snapshot=snapshot,
                storage=storage,
                settings=settings,
            )

        storage.generate_presigned_download_url.assert_not_called()

    def test_rejects_worker_source_provenance_mismatch_before_signing(
        self,
    ) -> None:
        snapshot = make_video_edit_snapshot()
        snapshot.parameters["source_resolution"] = {
            "origin": "brand_asset",
            "source_version_id": None,
            "source_brand_asset_id": str(uuid.uuid4()),
        }
        storage = MagicMock()

        with self.assertRaisesRegex(
            GenerationInputError,
            "inconsistent 'source_origin' provenance",
        ):
            prepare_video_generation_request(
                snapshot=snapshot,
                storage=storage,
                settings=Settings(
                    _env_file=None,
                    GENBLAZE_VIDEO_TO_VIDEO_ENABLED=True,
                ),
            )

        storage.generate_presigned_download_url.assert_not_called()

    def test_rejects_disabled_video_edit_job_before_signing(self) -> None:
        snapshot = make_video_edit_snapshot()
        storage = MagicMock()

        with self.assertRaisesRegex(
            GenerationInputError,
            "disabled by configuration",
        ):
            prepare_video_generation_request(
                snapshot=snapshot,
                storage=storage,
                settings=Settings(_env_file=None),
            )

        storage.generate_presigned_download_url.assert_not_called()

    def test_rejects_non_https_signed_source_url(self) -> None:
        snapshot = make_video_edit_snapshot()
        storage = MagicMock()
        storage.generate_presigned_download_url.return_value = (
            "http://s3.example.com/source.mp4?signature=temporary"
        )

        with self.assertRaisesRegex(
            GenerationInputError,
            "downloadable HTTPS URL",
        ):
            prepare_video_generation_request(
                snapshot=snapshot,
                storage=storage,
                settings=Settings(
                    _env_file=None,
                    GENBLAZE_VIDEO_TO_VIDEO_ENABLED=True,
                ),
            )

    def test_worker_controls_follow_registered_model_contract(self) -> None:
        parameters = {
            "duration": 4,
            "aspect_ratio": "16:9",
            "resolution": "720p",
        }

        self.assertEqual(
            video_provider_parameters(
                parameters,
                model="veo-3.1-fast-generate-001",
            ),
            parameters,
        )
        self.assertEqual(
            video_provider_parameters(
                parameters,
                model="wan2.7-videoedit",
            ),
            {},
        )

    def test_worker_failure_message_redacts_presigned_url_query(self) -> None:
        message = safe_worker_error_message(
            GenerationProviderError(
                "Provider rejected "
                "https://s3.example.com/source.mp4?"
                "X-Amz-Signature=secret&X-Amz-Expires=3600, retry later"
            )
        )

        self.assertNotIn("secret", message)
        self.assertNotIn("X-Amz-Expires", message)
        self.assertIn(
            "https://s3.example.com/source.mp4?redacted, retry later",
            message,
        )

    def test_selects_durable_artifact_and_enforces_size_limit(self) -> None:
        artifact = select_durable_video_artifact(
            make_result(),
            max_size_bytes=5000,
        )

        self.assertEqual(artifact.filename, "generated.mp4")
        self.assertEqual(artifact.content_type, "video/mp4")

        with self.assertRaisesRegex(
            GenerationProviderError,
            "configured size limit",
        ):
            select_durable_video_artifact(
                make_result(),
                max_size_bytes=4000,
            )

    def test_completed_metadata_keeps_inputs_and_omits_signed_urls(self) -> None:
        started_at = datetime(2026, 7, 15, 3, 0, tzinfo=UTC)
        completed_at = started_at + timedelta(minutes=5)
        source_asset_id = uuid.uuid4()
        source_version_id = uuid.uuid4()
        source_record = {
            "role": "source_creative",
            "storage_key": "campaigns/source/product.jpg",
            "filename": "product.jpg",
            "content_type": "image/jpeg",
            "media_kind": "image",
            "size_bytes": 2048,
            "sha256": "b" * 64,
            "source": "source_version_artifact",
            "storage_ownership": "source_asset_version",
            "source_asset_id": str(source_asset_id),
            "source_version_id": str(source_version_id),
            "source_version_number": 3,
        }
        snapshot = VideoJobSnapshot(
            id=uuid.uuid4(),
            asset_version_id=uuid.uuid4(),
            campaign_id=uuid.uuid4(),
            asset_id=uuid.uuid4(),
            version_number=1,
            provider="gmicloud",
            model="Veo3-Fast",
            prompt="Orbit around the product.",
            parameters={
                "duration": 4,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "input_mode": "image_to_video",
                "source_origin": "asset_version",
                "source_version_id": str(source_version_id),
                "source_resolution": {
                    "origin": "asset_version",
                    "source_version_id": str(source_version_id),
                    "source_brand_asset_id": None,
                },
                "source_input_assets": [source_record],
                "context_assets": [],
            },
            attempt_count=1,
            started_at=started_at,
            version_generation_metadata={"provenance": {"source": "submission"}},
        )
        artifact = DurableVideoArtifact(
            storage_key="campaigns/video/artifact/generated.mp4",
            filename="generated.mp4",
            content_type="video/mp4",
            size_bytes=4096,
            sha256="a" * 64,
            source_storage_key="sereneset-spark/genblaze/generated.mp4",
        )
        sidecar_storage_key = "campaigns/video/metadata.json"

        metadata = build_completed_generation_metadata(
            snapshot=snapshot,
            result=make_result(),
            artifact=artifact,
            completed_at=completed_at,
            sidecar_storage_key=sidecar_storage_key,
        )

        self.assertEqual(metadata["job"]["status"], "succeeded")
        self.assertEqual(metadata["provenance_schema_version"], 2)
        self.assertEqual(
            metadata["provenance_schema"],
            "sereneset.video-generation",
        )
        self.assertEqual(metadata["input_mode"], "image_to_video")
        self.assertEqual(
            metadata["source_resolution"]["source_version_id"],
            str(source_version_id),
        )
        self.assertEqual(metadata["input_assets"], [source_record])
        self.assertNotIn("url", metadata["input_assets"][0])
        self.assertEqual(
            metadata["artifact_flow"]["source"],
            "genblaze_b2_server_side_copy",
        )
        self.assertEqual(
            metadata["artifact_flow"]["storage_strategy"],
            "server_side_copy",
        )
        self.assertEqual(
            metadata["artifact_flow"]["source_storage_key"],
            artifact.source_storage_key,
        )
        self.assertEqual(
            metadata["artifact_flow"]["sha256"],
            artifact.sha256,
        )
        self.assertEqual(
            metadata["sidecar"]["storage_key"],
            sidecar_storage_key,
        )
        self.assertEqual(metadata["provenance"]["schema_version"], 2)
        self.assertEqual(
            metadata["request"]["generation_parameters"],
            {
                "duration": 4,
                "aspect_ratio": "16:9",
                "resolution": "720p",
            },
        )
        self.assertEqual(
            metadata["execution"]["parameters"],
            {
                "duration": 4,
                "aspect_ratio": "16:9",
                "resolution": "720p",
            },
        )
        self.assertFalse(
            metadata["execution"]["input_routing"]["context_assets_routed_to_provider"]
        )
        self.assertIn(
            "submission_provenance",
            metadata["provenance"],
        )

        context = VideoProvenanceContext(
            version_storage_key=sidecar_storage_key,
            campaign={
                "id": str(snapshot.campaign_id),
                "name": "Launch",
                "product": "Product",
                "audience": "Audience",
                "status": "drafting",
                "channels": ["Paid social"],
                "brand_inputs": [],
            },
            asset={
                "id": str(snapshot.asset_id),
                "title": "Launch video",
                "format": "video_concept",
                "channel": "Paid social",
                "status": "draft",
                "reviewer": None,
                "tags": ["video"],
                "summary": "Video draft",
            },
        )
        sidecar = build_video_provenance_sidecar(
            context=context,
            snapshot=snapshot,
            result=make_result(),
            artifact=artifact,
            generation_metadata=metadata,
            stored_at=completed_at,
        )

        self.assertEqual(sidecar["campaign"], context.campaign)
        self.assertEqual(sidecar["asset"], context.asset)
        self.assertEqual(sidecar["schema_version"], 2)
        self.assertEqual(
            sidecar["version"]["generation_metadata"],
            metadata,
        )
        self.assertEqual(
            sidecar["version"]["artifact_storage_key"],
            artifact.storage_key,
        )
        self.assertEqual(sidecar["version"]["label"], "Genblaze video 1")
        self.assertEqual(sidecar["stored_at"], completed_at.isoformat())

        storage = MagicMock()
        storage.upload_json.return_value = StoredObject(
            bucket="test-bucket",
            key=sidecar_storage_key,
            content_type="application/json",
            size=2048,
        )
        stored_sidecar = upload_video_provenance_sidecar(
            storage=storage,
            context=context,
            snapshot=snapshot,
            result=make_result(),
            artifact=artifact,
            generation_metadata=metadata,
            stored_at=completed_at,
        )

        self.assertEqual(stored_sidecar.key, sidecar_storage_key)
        upload_args = storage.upload_json.call_args.kwargs
        self.assertEqual(upload_args["data"], sidecar)
        self.assertEqual(
            upload_args["metadata"]["manifest_hash"],
            "manifest-hash",
        )

    def test_video_edit_provenance_attests_binding_without_signed_url(self) -> None:
        snapshot = make_video_edit_snapshot()
        signed_url = "https://s3.example/source.mp4?X-Amz-Signature=secret-token"
        snapshot.parameters["source_input_assets"][0]["url"] = signed_url
        result = make_result(
            model="wan2.7-videoedit",
            prompt=snapshot.prompt,
            input_mode="video_to_video",
            input_assets_parameter="external_inputs",
            provider_source_parameter="video",
            extra_metadata={"provider_debug": {"url": signed_url}},
            manifest_uri=(
                "https://s3.example/manifest.json?X-Amz-Signature=manifest-secret"
            ),
        )
        artifact = DurableVideoArtifact(
            storage_key="campaigns/video/artifact/generated.mp4",
            filename="generated.mp4",
            content_type="video/mp4",
            size_bytes=4096,
            sha256="a" * 64,
            source_storage_key="sereneset-spark/genblaze/generated.mp4",
        )

        metadata = build_completed_generation_metadata(
            snapshot=snapshot,
            result=result,
            artifact=artifact,
            completed_at=datetime(2026, 7, 22, 2, 0, tzinfo=UTC),
            sidecar_storage_key="campaigns/video/metadata.json",
        )

        self.assertEqual(metadata["provenance_schema_version"], 2)
        self.assertEqual(metadata["input_mode"], "video_to_video")
        self.assertEqual(
            metadata["source_resolution"],
            {
                "origin": "user_upload",
                "source_version_id": None,
                "source_brand_asset_id": None,
            },
        )
        self.assertEqual(metadata["execution"]["parameters"], {})
        routing = metadata["execution"]["input_routing"]
        self.assertEqual(routing["genblaze_input_parameter"], "external_inputs")
        self.assertEqual(routing["provider_source_parameter"], "video")
        self.assertEqual(
            routing["source_input"]["sha256"],
            "b" * 64,
        )
        self.assertEqual(
            routing["source_input"]["content_validation"]["container"],
            "mp4",
        )
        self.assertFalse(routing["context_assets_routed_to_provider"])
        self.assertFalse(routing["temporary_download_url_persisted"])
        self.assertEqual(
            metadata["manifest_uri"],
            "https://s3.example/manifest.json",
        )
        self.assertNotIn("url", metadata["assets"][0])
        serialized_metadata = json.dumps(metadata)
        self.assertNotIn("X-Amz-Signature", serialized_metadata)
        self.assertNotIn("secret-token", serialized_metadata)
        self.assertNotIn("manifest-secret", serialized_metadata)

    def test_refinement_completion_preserves_canonical_lineage(self) -> None:
        snapshot = make_video_refinement_snapshot()
        source_record = snapshot.parameters["source_input_assets"][0]
        based_on_version_id = source_record["source_version_id"]
        completed_at = datetime(2026, 7, 22, 4, 0, tzinfo=UTC)
        result = make_result(
            model=snapshot.model,
            prompt=snapshot.prompt,
            input_mode="video_to_video",
            input_assets_parameter="external_inputs",
            provider_source_parameter="video",
        )
        artifact = DurableVideoArtifact(
            storage_key="campaigns/video/artifact/refined.mp4",
            filename="refined.mp4",
            content_type="video/mp4",
            size_bytes=4096,
            sha256="c" * 64,
            source_storage_key="sereneset-spark/genblaze/refined.mp4",
        )

        metadata = build_completed_generation_metadata(
            snapshot=snapshot,
            result=result,
            artifact=artifact,
            completed_at=completed_at,
            sidecar_storage_key="campaigns/video/metadata.json",
        )

        self.assertEqual(metadata["operation"], "video_refinement")
        self.assertEqual(
            metadata["based_on_version_id"],
            based_on_version_id,
        )
        self.assertEqual(
            metadata["provenance"]["operation"],
            "video_refinement",
        )
        self.assertEqual(
            metadata["provenance"]["based_on_version_id"],
            based_on_version_id,
        )
        self.assertEqual(
            metadata["request"]["operation"],
            "video_refinement",
        )
        self.assertEqual(
            metadata["request"]["based_on_version_id"],
            based_on_version_id,
        )
        self.assertEqual(metadata["job"]["operation"], "video_refinement")
        self.assertEqual(
            metadata["execution"]["operation"],
            "video_refinement",
        )
        self.assertEqual(metadata["source_input_assets"], [source_record])
        self.assertNotIn("url", metadata["source_input_assets"][0])

        context = VideoProvenanceContext(
            version_storage_key="campaigns/video/metadata.json",
            campaign={"id": str(snapshot.campaign_id)},
            asset={"id": str(snapshot.asset_id)},
        )
        sidecar = build_video_provenance_sidecar(
            context=context,
            snapshot=snapshot,
            result=result,
            artifact=artifact,
            generation_metadata=metadata,
            stored_at=completed_at,
        )

        self.assertEqual(sidecar["operation"], "video_refinement")
        self.assertEqual(
            sidecar["based_on_version_id"],
            based_on_version_id,
        )
        self.assertEqual(sidecar["version"]["label"], "Video refinement 4")
        self.assertNotIn("url", sidecar["version"]["input_assets"][0])

    def test_refinement_rejects_cross_asset_lineage_before_signing(self) -> None:
        snapshot = make_video_refinement_snapshot()
        snapshot.parameters["source_input_assets"][0]["source_asset_id"] = str(
            uuid.uuid4()
        )
        storage = MagicMock()

        with self.assertRaisesRegex(
            GenerationInputError,
            "does not belong to the refined asset",
        ):
            prepare_video_generation_request(
                snapshot=snapshot,
                storage=storage,
                settings=Settings(
                    _env_file=None,
                    GENBLAZE_VIDEO_TO_VIDEO_ENABLED=True,
                    GENBLAZE_VIDEO_EDIT_MODEL="wan2.7-videoedit",
                ),
            )

        storage.generate_presigned_download_url.assert_not_called()

    def test_refinement_rejects_generation_controls_before_signing(self) -> None:
        snapshot = make_video_refinement_snapshot()
        snapshot.parameters["duration"] = 4
        storage = MagicMock()

        with self.assertRaisesRegex(
            GenerationInputError,
            "cannot include generation controls",
        ):
            prepare_video_generation_request(
                snapshot=snapshot,
                storage=storage,
                settings=Settings(
                    _env_file=None,
                    GENBLAZE_VIDEO_TO_VIDEO_ENABLED=True,
                    GENBLAZE_VIDEO_EDIT_MODEL="wan2.7-videoedit",
                ),
            )

        storage.generate_presigned_download_url.assert_not_called()

    def test_rejects_inconsistent_genblaze_provenance_before_storage(self) -> None:
        snapshot = make_video_edit_snapshot()
        result = make_result(
            model="wan2.7-videoedit",
            prompt=snapshot.prompt,
            input_mode="image_to_video",
            input_assets_parameter="external_inputs",
            provider_source_parameter="video",
        )

        with self.assertRaisesRegex(
            GenerationProviderError,
            "input mode does not match",
        ):
            validate_video_execution_provenance(
                snapshot=snapshot,
                result=result,
            )

    def test_finalizes_version_and_job_in_one_commit(self) -> None:
        completed_at = datetime(2026, 7, 15, 4, 0, tzinfo=UTC)
        job = make_job(status=GenerationJobStatus.running.value, attempt_count=1)
        job.started_at = completed_at - timedelta(minutes=5)
        snapshot = VideoJobSnapshot(
            id=job.id,
            asset_version_id=job.asset_version_id,
            campaign_id=job.asset_version.asset.campaign_id,
            asset_id=job.asset_version.asset.id,
            version_number=job.asset_version.version_number,
            provider=job.provider,
            model=job.model,
            prompt=job.prompt,
            parameters=job.parameters,
            attempt_count=job.attempt_count,
            started_at=job.started_at,
            version_generation_metadata=job.asset_version.generation_metadata,
        )
        artifact = DurableVideoArtifact(
            storage_key="sereneset-spark/genblaze/generated.mp4",
            filename="generated.mp4",
            content_type="video/mp4",
            size_bytes=4096,
            sha256="a" * 64,
        )
        db = MagicMock()
        db.scalar.return_value = job
        final_metadata = {"finalized": True}
        sidecar_storage_key = "campaigns/test/final-metadata.json"

        finalized = finalize_video_job_success(
            db,
            snapshot=snapshot,
            result=make_result(),
            artifact=artifact,
            generation_metadata=final_metadata,
            sidecar_storage_key=sidecar_storage_key,
            completed_at=completed_at,
        )

        self.assertTrue(finalized)
        self.assertEqual(job.status, GenerationJobStatus.succeeded.value)
        self.assertEqual(job.progress_percent, 100)
        self.assertEqual(job.provider_job_id, "provider-job-123")
        self.assertEqual(
            job.asset_version.artifact_storage_key,
            artifact.storage_key,
        )
        self.assertEqual(
            job.asset_version.generation_metadata,
            final_metadata,
        )
        self.assertEqual(job.asset_version.storage_key, sidecar_storage_key)
        self.assertEqual(job.asset_version.label, "Genblaze video 1")
        db.commit.assert_called_once_with()

    def test_finalizes_refinement_with_operation_aware_label(self) -> None:
        snapshot = make_video_refinement_snapshot()
        asset = Asset(
            id=snapshot.asset_id,
            campaign_id=snapshot.campaign_id,
            title="Launch video",
            format=AssetFormat.video_concept,
            channel="Paid social",
            status=ReviewStatus.draft,
            reviewer=None,
            tags=["video"],
            summary="Video refinement",
        )
        version = AssetVersion(
            id=snapshot.asset_version_id,
            asset_id=asset.id,
            version_number=snapshot.version_number,
            label="Queued video refinement 4",
            prompt=snapshot.prompt,
            model=snapshot.model,
            provider=snapshot.provider,
            storage_key="campaigns/video/metadata.json",
            generation_metadata=snapshot.version_generation_metadata,
        )
        version.asset = asset
        job = GenerationJob(
            id=snapshot.id,
            asset_version_id=version.id,
            kind="video",
            status=GenerationJobStatus.running.value,
            provider=snapshot.provider,
            model=snapshot.model,
            prompt=snapshot.prompt,
            parameters=snapshot.parameters,
            progress_percent=5,
            attempt_count=1,
        )
        job.asset_version = version
        artifact = DurableVideoArtifact(
            storage_key="campaigns/video/artifact/refined.mp4",
            filename="refined.mp4",
            content_type="video/mp4",
            size_bytes=4096,
            sha256="c" * 64,
        )
        db = MagicMock()
        db.scalar.return_value = job

        finalized = finalize_video_job_success(
            db,
            snapshot=snapshot,
            result=make_result(
                model=snapshot.model,
                prompt=snapshot.prompt,
            ),
            artifact=artifact,
            generation_metadata={"operation": "video_refinement"},
        )

        self.assertTrue(finalized)
        self.assertEqual(version.label, "Video refinement 4")
        db.commit.assert_called_once_with()

    def test_stores_video_with_b2_server_side_copy(self) -> None:
        campaign_id = uuid.uuid4()
        asset_id = uuid.uuid4()
        version_id = uuid.uuid4()
        job_id = uuid.uuid4()
        snapshot = VideoJobSnapshot(
            id=job_id,
            asset_version_id=version_id,
            campaign_id=campaign_id,
            asset_id=asset_id,
            version_number=2,
            provider="gmicloud",
            model="Veo3-Fast",
            prompt="Orbit around the product.",
            parameters={},
            attempt_count=1,
            started_at=None,
            version_generation_metadata={},
        )
        generated_artifact = DurableVideoArtifact(
            storage_key="sereneset-spark/genblaze/run/generated.mp4",
            filename="generated.mp4",
            content_type="video/mp4",
            size_bytes=500 * 1024 * 1024,
            sha256="a" * 64,
            source_storage_key=("sereneset-spark/genblaze/run/generated.mp4"),
        )
        destination_key = (
            f"campaigns/{campaign_id}/assets/{asset_id}/versions/"
            "v2/artifact/generated.mp4"
        )
        storage = MagicMock()
        storage.copy_object.return_value = StoredObject(
            bucket="test-bucket",
            key=destination_key,
            content_type="video/mp4",
            size=500 * 1024 * 1024,
            etag='"copy-etag"',
        )

        stored_artifact = store_video_artifact(
            snapshot=snapshot,
            artifact=generated_artifact,
            storage=storage,
            max_size_bytes=500 * 1024 * 1024,
        )

        self.assertEqual(stored_artifact.storage_key, destination_key)
        self.assertEqual(
            stored_artifact.source_storage_key,
            generated_artifact.storage_key,
        )
        copy_args = storage.copy_object.call_args.kwargs
        self.assertEqual(copy_args["source_key"], generated_artifact.storage_key)
        self.assertEqual(copy_args["destination_key"], destination_key)
        self.assertEqual(copy_args["max_size_bytes"], 500 * 1024 * 1024)
        self.assertEqual(copy_args["metadata"]["generation_job_id"], str(job_id))
        storage.download_bytes.assert_not_called()

    def test_cleanup_deletes_only_campaign_owned_keys_once(self) -> None:
        storage = MagicMock()

        cleanup_video_outputs(
            storage=storage,
            storage_keys=[
                "campaigns/video/artifact.mp4",
                "campaigns/video/metadata.json",
                "campaigns/video/artifact.mp4",
                "genblaze/source.mp4",
                None,
            ],
            protected_storage_keys=["genblaze/source.mp4"],
        )

        self.assertEqual(storage.delete_object.call_count, 2)
        storage.delete_object.assert_any_call(key="campaigns/video/artifact.mp4")
        storage.delete_object.assert_any_call(key="campaigns/video/metadata.json")

    def test_failure_is_safely_persisted(self) -> None:
        failed_at = datetime(2026, 7, 15, 5, 0, tzinfo=UTC)
        job = make_job(status=GenerationJobStatus.running.value, attempt_count=1)
        db = MagicMock()
        db.scalar.return_value = job

        marked = mark_video_job_failed(
            db,
            job_id=job.id,
            error_message="Provider unavailable",
            completed_at=failed_at,
        )

        self.assertTrue(marked)
        self.assertEqual(job.status, GenerationJobStatus.failed.value)
        self.assertEqual(job.error_message, "Provider unavailable")
        self.assertEqual(job.completed_at, failed_at)
        self.assertEqual(
            job.asset_version.generation_metadata["failure"]["message"],
            "Provider unavailable",
        )
        db.commit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
