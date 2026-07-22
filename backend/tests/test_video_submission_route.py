import unittest
import uuid
from unittest.mock import MagicMock, patch

from fastapi import HTTPException, status

from app.core.config import Settings
from app.main import app
from app.models.asset import Asset, AssetFormat, AssetVersion, ReviewStatus
from app.models.brand_asset import (
    BrandAsset,
    BrandAssetType,
    CampaignBrandAsset,
)
from app.models.campaign import Campaign
from app.models.generation_job import GenerationJobStatus
from app.schemas.generation_job import VideoGenerationCreate
from app.services.generation import VideoInputMode
from app.api.v1.routes.generation_jobs import (
    build_queued_video_models,
    campaign_brand_asset_input_record,
    get_source_version_or_404,
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


def stored_video_source() -> AssetVersion:
    source_asset = Asset(
        id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        title="Source video",
        format=AssetFormat.video_concept,
        channel="Paid social",
        status=ReviewStatus.draft,
        reviewer=None,
        tags=[],
        summary="Source video",
    )
    source_version = AssetVersion(
        id=uuid.uuid4(),
        asset_id=source_asset.id,
        version_number=1,
        label="Source video v1",
        prompt="Original motion",
        model="Veo3-Fast",
        provider="gmicloud",
        storage_key="campaigns/source/video-metadata.json",
        artifact_storage_key="campaigns/source/artifact/source.mp4",
        artifact_filename="source.mp4",
        artifact_content_type="video/mp4",
        artifact_size_bytes=10 * 1024 * 1024,
        generation_metadata={},
    )
    source_version.asset = source_asset
    return source_version


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
        self.assertEqual(len(version.inputs), 1)
        self.assertEqual(version.inputs[0].media_kind, "document")
        self.assertEqual(version.inputs[0].storage_key, brand_context["storage_key"])
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
        self.assertEqual(record["media_kind"], "image")
        self.assertEqual(record["sha256"], "b" * 64)
        self.assertEqual(record["role"], "source_creative")
        self.assertEqual(record["storage_ownership"], "source_asset_version")
        self.assertEqual(record["source_version_id"], str(source_version.id))

    def test_snapshots_stored_video_as_source_media(self) -> None:
        source_version = stored_video_source()

        record = source_version_input_record(source_version)

        self.assertEqual(record["content_type"], "video/mp4")
        self.assertEqual(record["media_kind"], "video")
        self.assertEqual(record["filename"], "source.mp4")
        self.assertEqual(record["source_asset_id"], str(source_version.asset_id))
        self.assertEqual(record["source_version_id"], str(source_version.id))

    def test_source_lookup_accepts_stored_video_version(self) -> None:
        campaign_id = uuid.uuid4()
        source_version = stored_video_source()
        source_version.asset.campaign_id = campaign_id
        db = MagicMock()
        db.scalar.return_value = source_version

        result = get_source_version_or_404(
            campaign_id=campaign_id,
            source_version_id=source_version.id,
            db=db,
        )

        self.assertIs(result, source_version)

    def test_snapshots_brand_asset_as_video_source(self) -> None:
        campaign_id = uuid.uuid4()
        brand_asset = BrandAsset(
            id=uuid.uuid4(),
            name="Product with sunset background",
            asset_type=BrandAssetType.product_image,
            description=None,
            usage_guidance="Keep the product still.",
            storage_key="brand-assets/product/original.jpg",
            filename="original.jpg",
            content_type="image/jpeg",
            size_bytes=2048,
            sha256="c" * 64,
            tags=[],
            source_url=None,
            is_active=True,
        )
        link = CampaignBrandAsset(
            id=uuid.uuid4(),
            campaign_id=campaign_id,
            brand_asset_id=brand_asset.id,
            role="product",
        )
        link.brand_asset = brand_asset

        record = campaign_brand_asset_input_record(
            link,
            role="source_creative",
        )

        self.assertEqual(record["role"], "source_creative")
        self.assertEqual(record["storage_key"], brand_asset.storage_key)
        self.assertEqual(record["brand_asset_id"], str(brand_asset.id))
        self.assertEqual(record["campaign_brand_asset_id"], str(link.id))
        self.assertEqual(record["storage_ownership"], "brand_asset")

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

    def test_submission_uses_attached_brand_image_as_provider_source(self) -> None:
        campaign_id = uuid.uuid4()
        brand_asset_id = uuid.uuid4()
        source_record = {
            "role": "source_creative",
            "storage_key": "brand-assets/product/original.jpg",
            "filename": "original.jpg",
            "content_type": "image/jpeg",
            "size_bytes": 2048,
            "sha256": "d" * 64,
            "source": "campaign_brand_asset",
            "storage_ownership": "brand_asset",
            "brand_asset_id": str(brand_asset_id),
        }
        db = MagicMock()
        db.get.return_value = Campaign(id=campaign_id)
        expected_response = object()

        with (
            patch(
                "app.api.v1.routes.generation_jobs."
                "get_source_brand_asset_link_or_404",
                return_value=object(),
            ),
            patch(
                "app.api.v1.routes.generation_jobs."
                "campaign_brand_asset_input_record",
                return_value=source_record,
            ),
            patch(
                "app.api.v1.routes.generation_jobs."
                "campaign_brand_context_assets",
                return_value=[],
            ) as context_assets,
            patch(
                "app.api.v1.routes.generation_jobs.load_video_submission",
                return_value=expected_response,
            ),
        ):
            response = submit_video_generation(
                campaign_id=campaign_id,
                video_in=video_request(
                    source_brand_asset_id=brand_asset_id,
                ),
                db=db,
                settings=settings(),
            )

        self.assertIs(response, expected_response)
        queued_asset = db.add.call_args.args[0]
        queued_job = queued_asset.versions[0].generation_job
        self.assertEqual(queued_job.parameters["input_mode"], "image_to_video")
        self.assertEqual(
            queued_job.parameters["source_brand_asset_id"],
            str(brand_asset_id),
        )
        self.assertEqual(
            queued_job.parameters["source_input_assets"],
            [source_record],
        )
        self.assertEqual(queued_job.parameters["context_assets"], [])
        context_assets.assert_called_once_with(
            campaign_id=campaign_id,
            db=db,
            exclude_brand_asset_id=brand_asset_id,
        )

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

    def test_video_source_fails_before_queueing_for_current_model(self) -> None:
        campaign_id = uuid.uuid4()
        source_version = stored_video_source()
        db = MagicMock()
        db.get.return_value = Campaign(id=campaign_id)

        with patch(
            "app.api.v1.routes.generation_jobs.get_source_version_or_404",
            return_value=source_version,
        ):
            with self.assertRaises(HTTPException) as raised:
                submit_video_generation(
                    campaign_id=campaign_id,
                    video_in=video_request(source_version_id=source_version.id),
                    db=db,
                    settings=settings(),
                )

        self.assertEqual(
            raised.exception.status_code,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
        self.assertIn(
            "does not support video source inputs",
            raised.exception.detail,
        )
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_verified_video_edit_model_stays_disabled_until_routed(self) -> None:
        campaign_id = uuid.uuid4()
        source_version = stored_video_source()
        db = MagicMock()
        db.get.return_value = Campaign(id=campaign_id)

        with patch(
            "app.api.v1.routes.generation_jobs.get_source_version_or_404",
            return_value=source_version,
        ):
            with self.assertRaises(HTTPException) as raised:
                submit_video_generation(
                    campaign_id=campaign_id,
                    video_in=video_request(
                        model="wan2.7-videoedit",
                        source_version_id=source_version.id,
                    ),
                    db=db,
                    settings=settings(),
                )

        self.assertEqual(
            raised.exception.status_code,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
        self.assertIn(
            "backend provider routing is not enabled yet",
            raised.exception.detail,
        )
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_unsupported_veo_controls_fail_before_database_write(self) -> None:
        invalid_controls = (
            {"duration_seconds": 2},
            {"aspect_ratio": "1:1"},
        )

        for controls in invalid_controls:
            with self.subTest(controls=controls):
                campaign_id = uuid.uuid4()
                db = MagicMock()
                db.get.return_value = Campaign(id=campaign_id)

                with self.assertRaises(HTTPException) as raised:
                    submit_video_generation(
                        campaign_id=campaign_id,
                        video_in=video_request(**controls),
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
