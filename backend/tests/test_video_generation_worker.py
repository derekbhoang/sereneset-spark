import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql

from app.models.asset import Asset, AssetFormat, AssetVersion, ReviewStatus
from app.models.generation_job import GenerationJob, GenerationJobStatus
from app.services.generation import (
    GeneratedAsset,
    GenerationProviderError,
    GenerationResult,
)
from app.services.storage import StoredObject
from app.workers.video_generation import (
    DurableVideoArtifact,
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
    recover_stale_video_jobs,
    select_durable_video_artifact,
    store_video_artifact,
    upload_video_provenance_sidecar,
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


def make_result() -> GenerationResult:
    return GenerationResult(
        provider="gmicloud",
        model="Veo3-Fast",
        prompt="Orbit around the product.",
        manifest_uri="b2://bucket/run/manifest.json",
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
            "genblaze": {"modality": "video", "asset_count": 1}
        },
    )


class VideoGenerationWorkerTests(unittest.TestCase):
    def test_claim_statement_uses_postgresql_skip_locked(self) -> None:
        compiled = str(
            build_video_job_claim_statement().compile(
                dialect=postgresql.dialect()
            )
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
        source_record = {
            "role": "source_creative",
            "storage_key": "campaigns/source/product.jpg",
            "filename": "product.jpg",
            "content_type": "image/jpeg",
            "size_bytes": 2048,
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
                "source_version_id": str(uuid.uuid4()),
                "source_input_assets": [source_record],
                "context_assets": [],
            },
            attempt_count=1,
            started_at=started_at,
            version_generation_metadata={
                "provenance": {"source": "submission"}
            },
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
        self.assertEqual(metadata["provenance_schema_version"], 1)
        self.assertEqual(metadata["input_mode"], "image_to_video")
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
        self.assertEqual(metadata["provenance"]["schema_version"], 1)
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
        self.assertEqual(
            sidecar["version"]["generation_metadata"],
            metadata,
        )
        self.assertEqual(
            sidecar["version"]["artifact_storage_key"],
            artifact.storage_key,
        )
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
            source_storage_key=(
                "sereneset-spark/genblaze/run/generated.mp4"
            ),
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
        storage.delete_object.assert_any_call(
            key="campaigns/video/artifact.mp4"
        )
        storage.delete_object.assert_any_call(
            key="campaigns/video/metadata.json"
        )

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
