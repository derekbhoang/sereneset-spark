import unittest
import uuid

from fastapi import HTTPException, status

from app.api.v1.routes.generation_jobs import (
    LockedVideoRefinementAsset,
    validate_latest_video_refinement_version,
)
from app.core.config import Settings
from app.models.asset import Asset, AssetFormat, AssetVersion, ReviewStatus
from app.models.generation_job import GenerationJob, GenerationJobStatus


def make_locked_asset(
    *,
    job_status: str | None = GenerationJobStatus.succeeded.value,
    storage_key: str | None = "campaigns/source/video.mp4",
    filename: str | None = "source.mp4",
    content_type: str | None = "video/mp4",
    size_bytes: int | None = 1024,
    sha256: str | None = "a" * 64,
    sha256_field: str = "source_sha256",
) -> LockedVideoRefinementAsset:
    asset = Asset(
        id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        title="Launch video",
        format=AssetFormat.video_concept,
        channel="Paid social",
        status=ReviewStatus.draft,
        reviewer=None,
        tags=["video"],
        summary="Launch video",
    )
    artifact_flow = {sha256_field: sha256} if sha256 is not None else {}
    version = AssetVersion(
        id=uuid.uuid4(),
        asset_id=asset.id,
        version_number=3,
        label="Video 3",
        prompt="Original video.",
        model="wan2.7-videoedit",
        provider="gmicloud",
        storage_key="campaigns/source/metadata.json",
        artifact_storage_key=storage_key,
        artifact_filename=filename,
        artifact_content_type=content_type,
        artifact_size_bytes=size_bytes,
        generation_metadata={"artifact_flow": artifact_flow},
    )
    generation_jobs: tuple[GenerationJob, ...] = ()
    if job_status is not None:
        job = GenerationJob(
            id=uuid.uuid4(),
            asset_version_id=version.id,
            kind="video",
            status=job_status,
            provider="gmicloud",
            model="wan2.7-videoedit",
            prompt="Original video.",
            parameters={"input_mode": "video_to_video"},
            progress_percent=100,
            attempt_count=1,
        )
        version.generation_job = job
        generation_jobs = (job,)

    asset.versions = [version]
    return LockedVideoRefinementAsset(
        asset=asset,
        latest_version=version,
        generation_jobs=generation_jobs,
    )


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


class VideoRefinementValidationTests(unittest.TestCase):
    def test_returns_normalized_snapshot_for_completed_latest_mp4(self) -> None:
        locked_asset = make_locked_asset(
            filename="SOURCE.MP4",
            content_type="Video/MP4; codecs=h264",
            sha256="A" * 64,
            sha256_field="sha256",
        )
        latest_version = locked_asset.latest_version
        assert latest_version is not None

        source = validate_latest_video_refinement_version(
            locked_asset=locked_asset,
            expected_latest_version_id=latest_version.id,
            settings=settings(),
        )

        self.assertIs(source.version, latest_version)
        self.assertEqual(source.storage_key, "campaigns/source/video.mp4")
        self.assertEqual(source.filename, "SOURCE.MP4")
        self.assertEqual(source.content_type, "video/mp4")
        self.assertEqual(source.size_bytes, 1024)
        self.assertEqual(source.sha256, "a" * 64)

    def test_accepts_legacy_stored_artifact_without_a_generation_job(self) -> None:
        locked_asset = make_locked_asset(job_status=None)
        latest_version = locked_asset.latest_version
        assert latest_version is not None

        source = validate_latest_video_refinement_version(
            locked_asset=locked_asset,
            expected_latest_version_id=latest_version.id,
            settings=settings(),
        )

        self.assertIs(source.version, latest_version)

    def test_rejects_asset_without_a_version(self) -> None:
        locked_asset = make_locked_asset()
        locked_asset = LockedVideoRefinementAsset(
            asset=locked_asset.asset,
            latest_version=None,
            generation_jobs=(),
        )

        with self.assertRaises(HTTPException) as raised:
            validate_latest_video_refinement_version(
                locked_asset=locked_asset,
                expected_latest_version_id=uuid.uuid4(),
                settings=settings(),
            )

        self.assertEqual(raised.exception.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            raised.exception.detail,
            "Video asset does not have a version to refine",
        )

    def test_rejects_stale_latest_version_guard(self) -> None:
        locked_asset = make_locked_asset()

        with self.assertRaises(HTTPException) as raised:
            validate_latest_video_refinement_version(
                locked_asset=locked_asset,
                expected_latest_version_id=uuid.uuid4(),
                settings=settings(),
            )

        self.assertEqual(raised.exception.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("refresh", str(raised.exception.detail).casefold())

    def test_rejects_latest_version_without_a_successful_job(self) -> None:
        job_states = (
            GenerationJobStatus.queued.value,
            GenerationJobStatus.running.value,
            GenerationJobStatus.failed.value,
            GenerationJobStatus.canceled.value,
        )

        for job_status in job_states:
            with self.subTest(job_status=job_status):
                locked_asset = make_locked_asset(job_status=job_status)
                latest_version = locked_asset.latest_version
                assert latest_version is not None

                with self.assertRaises(HTTPException) as raised:
                    validate_latest_video_refinement_version(
                        locked_asset=locked_asset,
                        expected_latest_version_id=latest_version.id,
                        settings=settings(),
                    )

                self.assertEqual(
                    raised.exception.status_code,
                    status.HTTP_409_CONFLICT,
                )

    def test_rejects_missing_or_invalid_artifact_metadata(self) -> None:
        invalid_cases = (
            ({"storage_key": None}, "stored artifact"),
            ({"filename": None}, "filename"),
            ({"filename": "source.mov"}, "MP4"),
            ({"content_type": "video/webm"}, "MP4"),
            ({"size_bytes": None}, "positive size"),
            ({"size_bytes": 0}, "positive size"),
            ({"sha256": None}, "SHA-256"),
            ({"sha256": "not-a-checksum"}, "SHA-256"),
        )

        for overrides, expected_detail in invalid_cases:
            with self.subTest(overrides=overrides):
                locked_asset = make_locked_asset(**overrides)
                latest_version = locked_asset.latest_version
                assert latest_version is not None

                with self.assertRaises(HTTPException) as raised:
                    validate_latest_video_refinement_version(
                        locked_asset=locked_asset,
                        expected_latest_version_id=latest_version.id,
                        settings=settings(),
                    )

                self.assertEqual(
                    raised.exception.status_code,
                    status.HTTP_409_CONFLICT,
                )
                self.assertIn(expected_detail, str(raised.exception.detail))

    def test_rejects_video_larger_than_configured_source_limit(self) -> None:
        locked_asset = make_locked_asset(size_bytes=1025)
        latest_version = locked_asset.latest_version
        assert latest_version is not None

        with self.assertRaises(HTTPException) as raised:
            validate_latest_video_refinement_version(
                locked_asset=locked_asset,
                expected_latest_version_id=latest_version.id,
                settings=settings(MAX_VIDEO_SOURCE_VIDEO_SIZE_BYTES=1024),
            )

        self.assertEqual(raised.exception.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("size limit", str(raised.exception.detail))


if __name__ == "__main__":
    unittest.main()
