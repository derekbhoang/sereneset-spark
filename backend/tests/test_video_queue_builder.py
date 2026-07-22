import unittest
import uuid

from app.api.v1.routes.generation_jobs import (
    ResolvedVideoSource,
    VideoSourceOrigin,
    build_queued_video_version_models,
)
from app.models.asset import Asset, AssetFormat, AssetVersion, ReviewStatus
from app.services.generation import VideoInputMode
from app.services.video_refinement import VideoGenerationOperation


def make_asset() -> tuple[Asset, AssetVersion]:
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
    previous_version = AssetVersion(
        id=uuid.uuid4(),
        asset_id=asset.id,
        version_number=1,
        label="Video 1",
        prompt="Original video.",
        model="veo-3.1-fast-generate-001",
        provider="gmicloud",
        storage_key="campaigns/source/metadata.json",
        artifact_storage_key="campaigns/source/video.mp4",
        artifact_filename="source.mp4",
        artifact_content_type="video/mp4",
        artifact_size_bytes=1024,
        generation_metadata={
            "artifact_flow": {"source_sha256": "a" * 64}
        },
    )
    asset.versions = [previous_version]
    return asset, previous_version


def refinement_source(
    *,
    asset: Asset,
    previous_version: AssetVersion,
    source_asset_id: uuid.UUID | None = None,
    source_version_id: uuid.UUID | None = None,
) -> tuple[ResolvedVideoSource, dict[str, object]]:
    resolved_version_id = source_version_id or previous_version.id
    input_record: dict[str, object] = {
        "role": "source_creative",
        "storage_key": previous_version.artifact_storage_key,
        "filename": previous_version.artifact_filename,
        "content_type": "video/mp4",
        "media_kind": "video",
        "size_bytes": previous_version.artifact_size_bytes,
        "sha256": "a" * 64,
        "source": "source_version_artifact",
        "storage_ownership": "source_asset_version",
        "source_asset_id": str(source_asset_id or asset.id),
        "source_version_id": str(resolved_version_id),
        "source_version_number": previous_version.version_number,
    }
    return (
        ResolvedVideoSource(
            origin=VideoSourceOrigin.asset_version,
            input_record=input_record,
            source_version_id=resolved_version_id,
        ),
        input_record,
    )


class VideoQueueBuilderTests(unittest.TestCase):
    def test_appends_refinement_version_and_job_to_existing_asset(self) -> None:
        asset, previous_version = make_asset()
        source, source_record = refinement_source(
            asset=asset,
            previous_version=previous_version,
        )
        context_record: dict[str, object] = {
            "role": "brand_guidelines",
            "storage_key": "brand-assets/guidelines.pdf",
            "filename": "guidelines.pdf",
            "content_type": "application/pdf",
            "media_kind": "document",
            "size_bytes": 4096,
            "sha256": "b" * 64,
            "source": "campaign_brand_asset",
            "storage_ownership": "brand_asset",
        }

        version, job = build_queued_video_version_models(
            asset=asset,
            version_number=2,
            prompt="Keep the product fixed and move only the background.",
            model="wan2.7-videoedit",
            input_mode=VideoInputMode.video_to_video,
            source=source,
            context_assets=[context_record],
            generation_parameters={},
            operation=VideoGenerationOperation.refinement,
        )

        self.assertEqual(asset.versions, [previous_version, version])
        self.assertEqual(version.asset_id, asset.id)
        self.assertEqual(version.version_number, 2)
        self.assertEqual(version.label, "Queued video refinement 2")
        self.assertIs(version.generation_job, job)
        self.assertIn(str(asset.id), version.storage_key)
        self.assertIn("v2", version.storage_key)
        self.assertEqual(job.parameters["operation"], "video_refinement")
        self.assertEqual(job.parameters["input_mode"], "video_to_video")
        self.assertEqual(
            job.parameters["source_version_id"],
            str(previous_version.id),
        )
        self.assertNotIn("duration", job.parameters)
        self.assertNotIn("aspect_ratio", job.parameters)
        self.assertNotIn("resolution", job.parameters)
        metadata = version.generation_metadata
        self.assertEqual(metadata["operation"], "video_refinement")
        self.assertEqual(
            metadata["based_on_version_id"],
            str(previous_version.id),
        )
        self.assertEqual(metadata["generation_parameters"], {})
        self.assertEqual(
            metadata["provenance"]["request"]["operation"],
            "video_refinement",
        )
        self.assertEqual(len(version.inputs), 2)
        self.assertEqual(version.inputs[0].source, "source_version_artifact")
        self.assertEqual(
            version.inputs[0].source_version_id,
            previous_version.id,
        )
        self.assertEqual(
            version.inputs[0].storage_ownership,
            "source_asset_version",
        )

        source_record["filename"] = "mutated.mp4"
        context_record["filename"] = "mutated.pdf"
        self.assertEqual(
            metadata["provenance"]["source_input_assets"][0]["filename"],
            "source.mp4",
        )
        self.assertEqual(
            metadata["provenance"]["context_assets"][0]["filename"],
            "guidelines.pdf",
        )

    def test_refinement_rejects_provider_controls(self) -> None:
        asset, previous_version = make_asset()
        source, _record = refinement_source(
            asset=asset,
            previous_version=previous_version,
        )

        with self.assertRaisesRegex(ValueError, "generation controls"):
            build_queued_video_version_models(
                asset=asset,
                version_number=2,
                prompt="Move the background.",
                model="wan2.7-videoedit",
                input_mode=VideoInputMode.video_to_video,
                source=source,
                context_assets=[],
                generation_parameters={"duration": 4},
                operation=VideoGenerationOperation.refinement,
            )

    def test_refinement_rejects_non_video_input_mode(self) -> None:
        asset, previous_version = make_asset()
        source, _record = refinement_source(
            asset=asset,
            previous_version=previous_version,
        )

        with self.assertRaisesRegex(ValueError, "video-to-video"):
            build_queued_video_version_models(
                asset=asset,
                version_number=2,
                prompt="Move the background.",
                model="wan2.7-videoedit",
                input_mode=VideoInputMode.image_to_video,
                source=source,
                context_assets=[],
                generation_parameters={},
                operation=VideoGenerationOperation.refinement,
            )

    def test_refinement_rejects_non_version_and_cross_asset_sources(self) -> None:
        asset, previous_version = make_asset()
        cross_asset_source, _record = refinement_source(
            asset=asset,
            previous_version=previous_version,
            source_asset_id=uuid.uuid4(),
        )
        invalid_sources = (
            ResolvedVideoSource(origin=VideoSourceOrigin.none),
            cross_asset_source,
        )

        for source in invalid_sources:
            with self.subTest(source_origin=source.origin):
                with self.assertRaises(ValueError):
                    build_queued_video_version_models(
                        asset=asset,
                        version_number=2,
                        prompt="Move the background.",
                        model="wan2.7-videoedit",
                        input_mode=VideoInputMode.video_to_video,
                        source=source,
                        context_assets=[],
                        generation_parameters={},
                        operation=VideoGenerationOperation.refinement,
                    )

    def test_refinement_rejects_inconsistent_source_version_provenance(
        self,
    ) -> None:
        asset, previous_version = make_asset()
        source, source_record = refinement_source(
            asset=asset,
            previous_version=previous_version,
        )
        source_record["source_version_id"] = str(uuid.uuid4())

        with self.assertRaisesRegex(ValueError, "provenance is inconsistent"):
            build_queued_video_version_models(
                asset=asset,
                version_number=2,
                prompt="Move the background.",
                model="wan2.7-videoedit",
                input_mode=VideoInputMode.video_to_video,
                source=source,
                context_assets=[],
                generation_parameters={},
                operation=VideoGenerationOperation.refinement,
            )


if __name__ == "__main__":
    unittest.main()
