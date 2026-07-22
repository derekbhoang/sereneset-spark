import unittest
import uuid
from unittest.mock import MagicMock, patch

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.api.v1.routes.generation_jobs import (
    LockedVideoRefinementAsset,
    submit_video_refinement,
)
from app.core.config import Settings
from app.main import app
from app.models.asset import Asset, AssetFormat, AssetVersion, ReviewStatus
from app.models.generation_job import GenerationJob, GenerationJobStatus
from app.schemas.generation_job import VideoRefinementCreate


def make_locked_asset() -> LockedVideoRefinementAsset:
    asset = Asset(
        id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        title="Launch video",
        format=AssetFormat.video_concept,
        channel="Paid social",
        status=ReviewStatus.approved,
        reviewer="Reviewer",
        tags=["video"],
        summary="Approved launch video",
    )
    version = AssetVersion(
        id=uuid.uuid4(),
        asset_id=asset.id,
        version_number=3,
        label="Video 3",
        prompt="Original video.",
        model="wan2.7-videoedit",
        provider="gmicloud",
        storage_key="campaigns/source/metadata.json",
        artifact_storage_key="campaigns/source/source.mp4",
        artifact_filename="source.mp4",
        artifact_content_type="video/mp4",
        artifact_size_bytes=2048,
        generation_metadata={
            "artifact_flow": {"source_sha256": "a" * 64},
        },
    )
    job = GenerationJob(
        id=uuid.uuid4(),
        asset_version_id=version.id,
        kind="video",
        status=GenerationJobStatus.succeeded.value,
        provider="gmicloud",
        model="wan2.7-videoedit",
        prompt="Original video.",
        parameters={"input_mode": "video_to_video"},
        progress_percent=100,
        attempt_count=1,
    )
    version.generation_job = job
    asset.versions = [version]
    return LockedVideoRefinementAsset(
        asset=asset,
        latest_version=version,
        generation_jobs=(job,),
    )


def refinement_request(
    latest_version_id: uuid.UUID,
) -> VideoRefinementCreate:
    return VideoRefinementCreate(
        prompt="Keep the product fixed and move only the background.",
        expected_latest_version_id=latest_version_id,
    )


def enabled_settings() -> Settings:
    return Settings(
        _env_file=None,
        GENBLAZE_VIDEO_EDIT_MODEL="wan2.7-videoedit",
        GENBLAZE_VIDEO_TO_VIDEO_ENABLED=True,
    )


class VideoRefinementRouteTests(unittest.TestCase):
    def test_queues_next_version_without_calling_provider_or_storage(self) -> None:
        locked_asset = make_locked_asset()
        latest_version = locked_asset.latest_version
        assert latest_version is not None
        db = MagicMock()
        expected_response = object()

        with (
            patch(
                "app.api.v1.routes.generation_jobs."
                "get_video_asset_for_refinement",
                return_value=locked_asset,
            ),
            patch(
                "app.api.v1.routes.generation_jobs."
                "campaign_brand_context_assets",
                return_value=[],
            ) as load_context,
            patch(
                "app.api.v1.routes.generation_jobs.load_video_submission",
                return_value=expected_response,
            ) as load_submission,
        ):
            response = submit_video_refinement(
                asset_id=locked_asset.asset.id,
                refinement_in=refinement_request(latest_version.id),
                db=db,
                settings=enabled_settings(),
            )

        self.assertIs(response, expected_response)
        self.assertEqual(locked_asset.asset.status, ReviewStatus.draft)
        self.assertEqual(len(locked_asset.asset.versions), 2)
        queued_version = locked_asset.asset.versions[-1]
        self.assertEqual(queued_version.version_number, 4)
        self.assertEqual(queued_version.label, "Queued video refinement 4")
        self.assertEqual(queued_version.model, "wan2.7-videoedit")
        self.assertEqual(len(queued_version.inputs), 1)
        self.assertEqual(
            queued_version.inputs[0].source_version_id,
            latest_version.id,
        )
        self.assertEqual(
            queued_version.inputs[0].storage_ownership,
            "source_asset_version",
        )
        queued_job = queued_version.generation_job
        assert queued_job is not None
        self.assertEqual(queued_job.status, GenerationJobStatus.queued.value)
        self.assertEqual(queued_job.parameters["operation"], "video_refinement")
        self.assertNotIn("duration", queued_job.parameters)
        db.add.assert_called_once_with(queued_version)
        db.commit.assert_called_once_with()
        db.rollback.assert_not_called()
        load_context.assert_called_once_with(
            campaign_id=locked_asset.asset.campaign_id,
            db=db,
        )
        load_submission.assert_called_once_with(
            asset_id=locked_asset.asset.id,
            job_id=queued_job.id,
            db=db,
        )

    def test_rejects_stale_client_before_queueing(self) -> None:
        locked_asset = make_locked_asset()
        db = MagicMock()

        with patch(
            "app.api.v1.routes.generation_jobs.get_video_asset_for_refinement",
            return_value=locked_asset,
        ):
            with self.assertRaises(HTTPException) as raised:
                submit_video_refinement(
                    asset_id=locked_asset.asset.id,
                    refinement_in=refinement_request(uuid.uuid4()),
                    db=db,
                    settings=enabled_settings(),
                )

        self.assertEqual(raised.exception.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("newer version", raised.exception.detail)
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_rejects_disabled_video_to_video_before_queueing(self) -> None:
        locked_asset = make_locked_asset()
        latest_version = locked_asset.latest_version
        assert latest_version is not None
        db = MagicMock()

        with patch(
            "app.api.v1.routes.generation_jobs.get_video_asset_for_refinement",
            return_value=locked_asset,
        ):
            with self.assertRaises(HTTPException) as raised:
                submit_video_refinement(
                    asset_id=locked_asset.asset.id,
                    refinement_in=refinement_request(latest_version.id),
                    db=db,
                    settings=Settings(
                        _env_file=None,
                        GENBLAZE_VIDEO_EDIT_MODEL="wan2.7-videoedit",
                        GENBLAZE_VIDEO_TO_VIDEO_ENABLED=False,
                    ),
                )

        self.assertEqual(
            raised.exception.status_code,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
        self.assertIn("disabled by configuration", raised.exception.detail)
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_rolls_back_version_number_conflict(self) -> None:
        locked_asset = make_locked_asset()
        latest_version = locked_asset.latest_version
        assert latest_version is not None
        db = MagicMock()
        db.commit.side_effect = IntegrityError(
            "insert asset_versions",
            {},
            Exception("duplicate version"),
        )

        with (
            patch(
                "app.api.v1.routes.generation_jobs."
                "get_video_asset_for_refinement",
                return_value=locked_asset,
            ),
            patch(
                "app.api.v1.routes.generation_jobs."
                "campaign_brand_context_assets",
                return_value=[],
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                submit_video_refinement(
                    asset_id=locked_asset.asset.id,
                    refinement_in=refinement_request(latest_version.id),
                    db=db,
                    settings=enabled_settings(),
                )

        self.assertEqual(raised.exception.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            raised.exception.detail,
            "Video refinement job could not be queued",
        )
        db.rollback.assert_called_once_with()

    def test_openapi_exposes_accepted_refinement_contract(self) -> None:
        operation = app.openapi()["paths"][
            "/api/v1/assets/{asset_id}/refine-video"
        ]["post"]

        self.assertIn("202", operation["responses"])
        self.assertEqual(
            operation["requestBody"]["content"]["application/json"]["schema"][
                "$ref"
            ],
            "#/components/schemas/VideoRefinementCreate",
        )
        self.assertEqual(
            operation["responses"]["202"]["content"]["application/json"][
                "schema"
            ]["$ref"],
            "#/components/schemas/VideoGenerationSubmissionRead",
        )


if __name__ == "__main__":
    unittest.main()
