import unittest
import uuid

from fastapi import HTTPException, status

from app.api.v1.routes.generation_jobs import (
    LockedVideoRefinementAsset,
    ensure_video_refinement_job_slot_available,
)
from app.models.asset import Asset, AssetFormat, ReviewStatus
from app.models.generation_job import GenerationJob, GenerationJobStatus


def make_job(job_status: str) -> GenerationJob:
    return GenerationJob(
        id=uuid.uuid4(),
        asset_version_id=uuid.uuid4(),
        kind="video",
        status=job_status,
        provider="gmicloud",
        model="wan2.7-videoedit",
        prompt="Move only the background.",
        parameters={"input_mode": "video_to_video"},
        progress_percent=0,
        attempt_count=0,
    )


def locked_asset_with_jobs(
    *jobs: GenerationJob,
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
    asset.versions = []
    return LockedVideoRefinementAsset(
        asset=asset,
        latest_version=None,
        generation_jobs=jobs,
    )


class VideoRefinementJobSlotTests(unittest.TestCase):
    def test_allows_refinement_without_existing_jobs(self) -> None:
        ensure_video_refinement_job_slot_available(locked_asset_with_jobs())

    def test_allows_refinement_when_every_job_is_terminal(self) -> None:
        locked_asset = locked_asset_with_jobs(
            make_job(GenerationJobStatus.succeeded.value),
            make_job(GenerationJobStatus.failed.value),
            make_job(GenerationJobStatus.canceled.value),
        )

        ensure_video_refinement_job_slot_available(locked_asset)

    def test_rejects_each_active_job_status(self) -> None:
        for job_status in (
            GenerationJobStatus.queued.value,
            GenerationJobStatus.running.value,
        ):
            with self.subTest(job_status=job_status):
                locked_asset = locked_asset_with_jobs(make_job(job_status))

                with self.assertRaises(HTTPException) as raised:
                    ensure_video_refinement_job_slot_available(locked_asset)

                self.assertEqual(
                    raised.exception.status_code,
                    status.HTTP_409_CONFLICT,
                )
                self.assertEqual(
                    raised.exception.detail,
                    "Video asset already has a queued or running generation job",
                )

    def test_rejects_active_job_from_any_asset_version(self) -> None:
        locked_asset = locked_asset_with_jobs(
            make_job(GenerationJobStatus.succeeded.value),
            make_job(GenerationJobStatus.queued.value),
            make_job(GenerationJobStatus.failed.value),
        )

        with self.assertRaises(HTTPException):
            ensure_video_refinement_job_slot_available(locked_asset)


if __name__ == "__main__":
    unittest.main()
