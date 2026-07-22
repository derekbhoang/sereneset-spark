import unittest
import uuid
from unittest.mock import MagicMock

from fastapi import HTTPException, status
from sqlalchemy.dialects import postgresql

from app.api.v1.routes.generation_jobs import (
    get_video_asset_for_refinement,
)
from app.models.asset import Asset, AssetFormat, AssetVersion, ReviewStatus
from app.models.generation_job import GenerationJob, GenerationJobStatus


def make_asset(*, asset_format: AssetFormat) -> Asset:
    return Asset(
        id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        title="Launch video",
        format=asset_format,
        channel="Paid social",
        status=ReviewStatus.draft,
        reviewer=None,
        tags=["video"],
        summary="Launch video",
    )


def make_version(*, asset: Asset, version_number: int) -> AssetVersion:
    return AssetVersion(
        id=uuid.uuid4(),
        asset_id=asset.id,
        version_number=version_number,
        label=f"Video {version_number}",
        prompt="Move the background.",
        model="wan2.7-videoedit",
        provider="gmicloud",
        storage_key=f"campaigns/version-{version_number}/metadata.json",
        artifact_storage_key=f"campaigns/version-{version_number}/video.mp4",
        artifact_filename="video.mp4",
        artifact_content_type="video/mp4",
        artifact_size_bytes=1024,
        generation_metadata={},
    )


def make_job(*, version: AssetVersion, job_status: str) -> GenerationJob:
    return GenerationJob(
        id=uuid.uuid4(),
        asset_version_id=version.id,
        kind="video",
        status=job_status,
        provider="gmicloud",
        model="wan2.7-videoedit",
        prompt="Move the background.",
        parameters={"input_mode": "video_to_video"},
        progress_percent=0,
        attempt_count=0,
    )


class VideoRefinementLoadingTests(unittest.TestCase):
    def test_locks_video_asset_and_returns_latest_version_and_jobs(self) -> None:
        asset = make_asset(asset_format=AssetFormat.video_concept)
        version_one = make_version(asset=asset, version_number=1)
        version_three = make_version(asset=asset, version_number=3)
        version_two = make_version(asset=asset, version_number=2)
        version_one.generation_job = make_job(
            version=version_one,
            job_status=GenerationJobStatus.succeeded.value,
        )
        version_three.generation_job = make_job(
            version=version_three,
            job_status=GenerationJobStatus.queued.value,
        )
        asset.versions = [version_one, version_three, version_two]
        db = MagicMock()
        db.scalar.return_value = asset

        result = get_video_asset_for_refinement(asset_id=asset.id, db=db)

        statement = db.scalar.call_args.args[0]
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertIn("FOR UPDATE OF assets", str(compiled))
        self.assertIn(asset.id, compiled.params.values())
        self.assertGreaterEqual(len(statement._with_options), 2)
        self.assertIs(result.asset, asset)
        self.assertIs(result.latest_version, version_three)
        self.assertEqual(
            result.generation_jobs,
            (version_one.generation_job, version_three.generation_job),
        )

    def test_returns_context_when_video_asset_has_no_versions(self) -> None:
        asset = make_asset(asset_format=AssetFormat.video_concept)
        asset.versions = []
        db = MagicMock()
        db.scalar.return_value = asset

        result = get_video_asset_for_refinement(asset_id=asset.id, db=db)

        self.assertIs(result.asset, asset)
        self.assertIsNone(result.latest_version)
        self.assertEqual(result.generation_jobs, ())

    def test_returns_not_found_when_asset_does_not_exist(self) -> None:
        db = MagicMock()
        db.scalar.return_value = None

        with self.assertRaises(HTTPException) as raised:
            get_video_asset_for_refinement(asset_id=uuid.uuid4(), db=db)

        self.assertEqual(raised.exception.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(raised.exception.detail, "Asset not found")

    def test_rejects_non_video_asset_formats(self) -> None:
        for asset_format in (AssetFormat.image, AssetFormat.copy):
            with self.subTest(asset_format=asset_format):
                asset = make_asset(asset_format=asset_format)
                asset.versions = []
                db = MagicMock()
                db.scalar.return_value = asset

                with self.assertRaises(HTTPException) as raised:
                    get_video_asset_for_refinement(asset_id=asset.id, db=db)

                self.assertEqual(
                    raised.exception.status_code,
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                )
                self.assertEqual(
                    raised.exception.detail,
                    "Only video concept assets can be refined",
                )


if __name__ == "__main__":
    unittest.main()
