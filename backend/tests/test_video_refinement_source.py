import unittest
import uuid

from app.api.v1.routes.generation_jobs import (
    ValidatedVideoRefinementSource,
    VideoSourceOrigin,
    build_video_refinement_source,
    build_queued_video_version_models,
)
from app.models.asset import Asset, AssetFormat, AssetVersion, ReviewStatus
from app.services.generation import VideoInputMode
from app.services.video_refinement import VideoGenerationOperation


def make_asset_and_source_version() -> tuple[Asset, AssetVersion]:
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
        model="veo-3.1-fast-generate-001",
        provider="gmicloud",
        storage_key="campaigns/source/metadata.json",
        artifact_storage_key="campaigns/source/video.mp4",
        artifact_filename="source.mp4",
        artifact_content_type="video/mp4",
        artifact_size_bytes=2048,
        generation_metadata={
            "artifact_flow": {"source_sha256": "a" * 64}
        },
    )
    asset.versions = [version]
    return asset, version


def validated_source(
    version: AssetVersion,
) -> ValidatedVideoRefinementSource:
    return ValidatedVideoRefinementSource(
        version=version,
        storage_key="campaigns/source/video.mp4",
        filename="source.mp4",
        content_type="video/mp4",
        size_bytes=2048,
        sha256="a" * 64,
    )


class VideoRefinementSourceTests(unittest.TestCase):
    def test_builds_durable_source_version_snapshot(self) -> None:
        asset, version = make_asset_and_source_version()

        source = build_video_refinement_source(validated_source(version))

        self.assertEqual(source.origin, VideoSourceOrigin.asset_version)
        self.assertEqual(source.source_version_id, version.id)
        self.assertIsNotNone(source.input_record)
        assert source.input_record is not None
        self.assertEqual(
            source.input_record,
            {
                "role": "source_creative",
                "storage_key": "campaigns/source/video.mp4",
                "filename": "source.mp4",
                "content_type": "video/mp4",
                "media_kind": "video",
                "size_bytes": 2048,
                "sha256": "a" * 64,
                "source": "source_version_artifact",
                "storage_ownership": "source_asset_version",
                "source_asset_id": str(asset.id),
                "source_version_id": str(version.id),
                "source_version_number": 3,
            },
        )
        self.assertNotIn("url", source.input_record)
        self.assertNotIn("content_validation", source.input_record)

    def test_snapshot_does_not_follow_later_version_mutation(self) -> None:
        _asset, version = make_asset_and_source_version()
        source = build_video_refinement_source(validated_source(version))
        assert source.input_record is not None

        version.artifact_filename = "mutated.mp4"
        version.artifact_storage_key = "campaigns/mutated/video.mp4"
        version.version_number = 99

        self.assertEqual(source.input_record["filename"], "source.mp4")
        self.assertEqual(
            source.input_record["storage_key"],
            "campaigns/source/video.mp4",
        )
        self.assertEqual(source.input_record["source_version_number"], 3)

    def test_snapshot_builds_non_owning_refinement_input(self) -> None:
        asset, version = make_asset_and_source_version()
        source = build_video_refinement_source(validated_source(version))

        refinement, _job = build_queued_video_version_models(
            asset=asset,
            version_number=4,
            prompt="Move only the background.",
            model="wan2.7-videoedit",
            input_mode=VideoInputMode.video_to_video,
            source=source,
            context_assets=[],
            generation_parameters={},
            operation=VideoGenerationOperation.refinement,
        )

        self.assertEqual(len(refinement.inputs), 1)
        version_input = refinement.inputs[0]
        self.assertEqual(version_input.source_asset_id, asset.id)
        self.assertEqual(version_input.source_version_id, version.id)
        self.assertEqual(version_input.source_version_number, 3)
        self.assertEqual(
            version_input.storage_ownership,
            "source_asset_version",
        )

    def test_rejects_invalid_source_version_number(self) -> None:
        _asset, version = make_asset_and_source_version()
        version.version_number = 0

        with self.assertRaisesRegex(ValueError, "must be positive"):
            build_video_refinement_source(validated_source(version))


if __name__ == "__main__":
    unittest.main()
