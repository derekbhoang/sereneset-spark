import unittest
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

from fastapi import HTTPException, status

from app.api.v1.routes.generation_jobs import (
    cancel_campaign_generation_job,
    get_campaign_generation_job,
    list_campaign_generation_jobs,
    retry_campaign_generation_job,
)
from app.main import app
from app.models.asset import Asset, AssetFormat, AssetVersion, ReviewStatus
from app.models.campaign import Campaign
from app.models.generation_job import GenerationJob, GenerationJobStatus


def make_job(
    *,
    campaign_id: uuid.UUID | None = None,
    job_status: str = GenerationJobStatus.queued.value,
    attempt_count: int = 0,
) -> GenerationJob:
    now = datetime.now(UTC)
    asset = Asset(
        id=uuid.uuid4(),
        campaign_id=campaign_id or uuid.uuid4(),
        title="Launch video",
        format=AssetFormat.video_concept,
        channel="Paid social",
        status=ReviewStatus.draft,
        reviewer=None,
        tags=["video"],
        summary="Video draft",
        created_at=now,
        updated_at=now,
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
            "failure": {"message": "Previous failure"},
            "provenance": {"source": "submission"},
        },
    )
    version.asset = asset
    job = GenerationJob(
        id=uuid.uuid4(),
        asset_version_id=version.id,
        kind="video",
        status=job_status,
        provider="gmicloud",
        model="Veo3-Fast",
        prompt="Orbit around the product.",
        parameters={"duration": 4},
        progress_percent=0,
        provider_job_id=None,
        attempt_count=attempt_count,
        error_message=(
            "Provider unavailable"
            if job_status == GenerationJobStatus.failed.value
            else None
        ),
        started_at=None,
        completed_at=(
            now
            if job_status
            in {
                GenerationJobStatus.failed.value,
                GenerationJobStatus.canceled.value,
            }
            else None
        ),
        created_at=now,
        updated_at=now,
    )
    job.asset_version = version
    return job


class GenerationJobRouteTests(unittest.TestCase):
    def test_lists_campaign_jobs_with_filters_and_pagination(self) -> None:
        campaign_id = uuid.uuid4()
        jobs = [
            make_job(campaign_id=campaign_id),
            make_job(
                campaign_id=campaign_id,
                job_status=GenerationJobStatus.failed.value,
            ),
        ]
        scalar_result = MagicMock()
        scalar_result.all.return_value = jobs
        db = MagicMock()
        db.get.return_value = Campaign(id=campaign_id)
        db.scalars.return_value = scalar_result

        response = list_campaign_generation_jobs(
            campaign_id=campaign_id,
            status_filter=GenerationJobStatus.failed,
            offset=10,
            limit=25,
            db=db,
        )

        self.assertEqual(response, jobs)
        db.get.assert_called_once_with(Campaign, campaign_id)
        statement = db.scalars.call_args.args[0]
        compiled_parameters = statement.compile().params
        self.assertIn(GenerationJobStatus.failed.value, compiled_parameters.values())
        self.assertIn(10, compiled_parameters.values())
        self.assertIn(25, compiled_parameters.values())

    def test_get_is_scoped_and_returns_not_found(self) -> None:
        campaign_id = uuid.uuid4()
        job = make_job(campaign_id=campaign_id)
        db = MagicMock()
        db.scalar.return_value = job

        response = get_campaign_generation_job(
            campaign_id=campaign_id,
            job_id=job.id,
            db=db,
        )

        self.assertIs(response, job)

        db.scalar.return_value = None
        with self.assertRaises(HTTPException) as raised:
            get_campaign_generation_job(
                campaign_id=campaign_id,
                job_id=uuid.uuid4(),
                db=db,
            )

        self.assertEqual(raised.exception.status_code, status.HTTP_404_NOT_FOUND)

    def test_cancel_queued_job_updates_version_provenance(self) -> None:
        campaign_id = uuid.uuid4()
        job = make_job(campaign_id=campaign_id)
        db = MagicMock()
        db.scalar.return_value = job

        response = cancel_campaign_generation_job(
            campaign_id=campaign_id,
            job_id=job.id,
            db=db,
        )

        self.assertIs(response, job)
        self.assertEqual(job.status, GenerationJobStatus.canceled.value)
        self.assertIsNotNone(job.completed_at)
        self.assertEqual(job.asset_version.label, "Canceled Genblaze video")
        metadata = job.asset_version.generation_metadata
        self.assertEqual(metadata["job"]["status"], "canceled")
        self.assertEqual(metadata["job_transitions"][-1]["event"], "canceled")
        self.assertIn("cancellation", metadata)
        self.assertNotIn("failure", metadata)
        db.commit.assert_called_once_with()

    def test_cancel_rejects_running_or_completed_job(self) -> None:
        for job_status in (
            GenerationJobStatus.running.value,
            GenerationJobStatus.failed.value,
            GenerationJobStatus.succeeded.value,
        ):
            with self.subTest(job_status=job_status):
                job = make_job(job_status=job_status)
                db = MagicMock()
                db.scalar.return_value = job

                with self.assertRaises(HTTPException) as raised:
                    cancel_campaign_generation_job(
                        campaign_id=job.asset_version.asset.campaign_id,
                        job_id=job.id,
                        db=db,
                    )

                self.assertEqual(
                    raised.exception.status_code,
                    status.HTTP_409_CONFLICT,
                )
                db.commit.assert_not_called()

    def test_retry_failed_job_preserves_attempt_count_and_requeues(self) -> None:
        campaign_id = uuid.uuid4()
        job = make_job(
            campaign_id=campaign_id,
            job_status=GenerationJobStatus.failed.value,
            attempt_count=2,
        )
        db = MagicMock()
        db.scalar.return_value = job

        response = retry_campaign_generation_job(
            campaign_id=campaign_id,
            job_id=job.id,
            db=db,
        )

        self.assertIs(response, job)
        self.assertEqual(job.status, GenerationJobStatus.queued.value)
        self.assertEqual(job.attempt_count, 2)
        self.assertIsNone(job.error_message)
        self.assertIsNone(job.started_at)
        self.assertIsNone(job.completed_at)
        self.assertEqual(job.asset_version.label, "Queued Genblaze video")
        metadata = job.asset_version.generation_metadata
        self.assertEqual(metadata["job"]["status"], "queued")
        self.assertEqual(metadata["job_transitions"][-1]["event"], "retried")
        self.assertNotIn("failure", metadata)
        self.assertNotIn("cancellation", metadata)
        db.commit.assert_called_once_with()

    def test_retry_rejects_queued_running_or_succeeded_job(self) -> None:
        for job_status in (
            GenerationJobStatus.queued.value,
            GenerationJobStatus.running.value,
            GenerationJobStatus.succeeded.value,
        ):
            with self.subTest(job_status=job_status):
                job = make_job(job_status=job_status)
                db = MagicMock()
                db.scalar.return_value = job

                with self.assertRaises(HTTPException) as raised:
                    retry_campaign_generation_job(
                        campaign_id=job.asset_version.asset.campaign_id,
                        job_id=job.id,
                        db=db,
                    )

                self.assertEqual(
                    raised.exception.status_code,
                    status.HTTP_409_CONFLICT,
                )
                db.commit.assert_not_called()

    def test_openapi_exposes_job_routes_and_public_status_filter(self) -> None:
        paths = app.openapi()["paths"]
        collection_path = "/api/v1/campaigns/{campaign_id}/generation-jobs"
        detail_path = (
            "/api/v1/campaigns/{campaign_id}/generation-jobs/{job_id}"
        )
        cancel_path = f"{detail_path}/cancel"
        retry_path = f"{detail_path}/retry"

        self.assertIn("get", paths[collection_path])
        parameters = paths[collection_path]["get"]["parameters"]
        self.assertIn("status", {parameter["name"] for parameter in parameters})
        self.assertIn("get", paths[detail_path])
        self.assertIn("post", paths[cancel_path])
        self.assertIn("post", paths[retry_path])
        self.assertIn("202", paths[retry_path]["post"]["responses"])


if __name__ == "__main__":
    unittest.main()
