import unittest
import uuid
from unittest.mock import MagicMock, patch

from fastapi import HTTPException, status

from app.core.config import Settings
from app.main import app
from app.models.asset import Asset, AssetFormat, AssetVersion, ReviewStatus
from app.models.campaign import Campaign
from app.models.generation_job import GenerationJobStatus
from app.schemas.generation_job import VideoGenerationCreate
from app.services.generation import VideoInputMode
from app.api.v1.routes.generation_jobs import (
    build_queued_video_models,
    source_version_input_record,
    submit_video_generation,
)


def video_request(**overrides: object) -> VideoGenerationCreate:
    payload: dict[str, object] = {
        "channel": "Paid social",
        "prompt": "Slowly orbit around the product.",
    }
    payload.update(overrides)
    return VideoGenerationCreate.model_validate(payload)


def settings() -> Settings:
    return Settings(_env_file=None)


class VideoSubmissionRouteTests(unittest.TestCase):
    def test_builds_queued_asset_version_and_job(self) -> None:
        campaign_id = uuid.uuid4()
        brand_context = {
            "role": "brand_reference",
            "storage_key": "brand-assets/guidelines.pdf",
            "filename": "guidelines.pdf",
            "content_type": "application/pdf",
            "size_bytes": 4096,
            "sha256": "a" * 64,
            "source": "campaign_brand_asset",
        }

        asset, version, job = build_queued_video_models(
            campaign_id=campaign_id,
            video_in=video_request(tags=["launch", "video"]),
            model="Veo3-Fast",
            input_mode=VideoInputMode.text_to_video,
            source_inputs=[],
            context_assets=[brand_context],
        )

        self.assertEqual(asset.format, AssetFormat.video_concept)
        self.assertEqual(asset.status, ReviewStatus.draft)
        self.assertEqual(asset.tags, ["genblaze", "video", "launch"])
        self.assertEqual(asset.versions, [version])
        self.assertIs(version.generation_job, job)
        self.assertIsNone(version.artifact_storage_key)
        self.assertEqual(job.status, GenerationJobStatus.queued.value)
        self.assertEqual(job.parameters["input_mode"], "text_to_video")
        self.assertEqual(job.parameters["context_assets"], [brand_context])
        self.assertEqual(
            version.generation_metadata["job"]["id"],
            str(job.id),
        )
        self.assertIn(str(asset.id), version.storage_key)

    def test_snapshots_source_version_artifact_without_taking_ownership(self) -> None:
        source_asset = Asset(
            id=uuid.uuid4(),
            campaign_id=uuid.uuid4(),
            title="Source image",
            format=AssetFormat.image,
            channel="Paid social",
            status=ReviewStatus.draft,
            reviewer=None,
            tags=[],
            summary="Source",
        )
        source_version = AssetVersion(
            id=uuid.uuid4(),
            asset_id=source_asset.id,
            version_number=3,
            label="Source v3",
            prompt="Product image",
            model="seedream-5.0-lite",
            provider="gmicloud",
            storage_key="campaigns/source/metadata.json",
            artifact_storage_key="campaigns/source/artifact/product.webp",
            artifact_filename="product.webp",
            artifact_content_type="application/octet-stream",
            artifact_size_bytes=2048,
            generation_metadata={
                "artifact_flow": {"source_sha256": "b" * 64}
            },
        )
        source_version.asset = source_asset

        record = source_version_input_record(source_version)

        self.assertEqual(record["content_type"], "image/webp")
        self.assertEqual(record["sha256"], "b" * 64)
        self.assertEqual(record["role"], "source_creative")
        self.assertEqual(record["storage_ownership"], "source_asset_version")
        self.assertEqual(record["source_version_id"], str(source_version.id))

    def test_submission_queues_without_calling_generation_or_storage(self) -> None:
        campaign_id = uuid.uuid4()
        db = MagicMock()
        db.get.return_value = Campaign(id=campaign_id)
        db.scalars.return_value.all.return_value = []
        expected_response = object()

        with patch(
            "app.api.v1.routes.generation_jobs.load_video_submission",
            return_value=expected_response,
        ) as load_submission:
            response = submit_video_generation(
                campaign_id=campaign_id,
                video_in=video_request(),
                db=db,
                settings=settings(),
            )

        self.assertIs(response, expected_response)
        db.add.assert_called_once()
        db.commit.assert_called_once()
        queued_asset = db.add.call_args.args[0]
        self.assertEqual(queued_asset.format, AssetFormat.video_concept)
        load_submission.assert_called_once()

    def test_image_required_model_fails_before_database_write(self) -> None:
        campaign_id = uuid.uuid4()
        db = MagicMock()
        db.get.return_value = Campaign(id=campaign_id)

        with self.assertRaises(HTTPException) as raised:
            submit_video_generation(
                campaign_id=campaign_id,
                video_in=video_request(
                    model="Kling-Image2Video-V2.1-Master"
                ),
                db=db,
                settings=settings(),
            )

        self.assertEqual(
            raised.exception.status_code,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_openapi_exposes_accepted_submission_contract(self) -> None:
        operation = app.openapi()["paths"][
            "/api/v1/campaigns/{campaign_id}/assets/generate-video"
        ]["post"]

        self.assertIn("202", operation["responses"])
        self.assertEqual(
            operation["responses"]["202"]["content"]["application/json"][
                "schema"
            ]["$ref"],
            "#/components/schemas/VideoGenerationSubmissionRead",
        )


if __name__ == "__main__":
    unittest.main()
