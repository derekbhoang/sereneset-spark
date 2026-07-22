import unittest
from unittest.mock import patch

from app.services.generation import (
    build_video_input_plan,
    GenerationInputError,
    VideoInputMode,
    VideoModelInputRequirement,
    VideoSourceMediaKind,
    validate_video_input_assets,
    video_model_input_requirement,
    video_source_media_kind,
)


def source_image(**overrides: object) -> dict[str, object]:
    input_asset: dict[str, object] = {
        "role": "source_creative",
        "url": "https://example.com/product.jpg",
        "filename": "product.jpg",
        "content_type": "image/jpeg",
        "size_bytes": 1024,
    }
    input_asset.update(overrides)
    return input_asset


def source_video(**overrides: object) -> dict[str, object]:
    input_asset: dict[str, object] = {
        "role": "source_creative",
        "url": "https://example.com/source.mp4",
        "filename": "source.mp4",
        "content_type": "video/mp4",
        "size_bytes": 10 * 1024 * 1024,
    }
    input_asset.update(overrides)
    return input_asset


class VideoInputRuleTests(unittest.TestCase):
    def test_resolves_text_and_image_modes(self) -> None:
        self.assertEqual(
            validate_video_input_assets(model="Veo3-Fast", input_assets=[]),
            VideoInputMode.text_to_video,
        )
        self.assertEqual(
            validate_video_input_assets(
                model="Veo3-Fast",
                input_assets=[source_image()],
            ),
            VideoInputMode.image_to_video,
        )

    def test_classifies_known_model_families(self) -> None:
        expectations = {
            "Kling-Image2Video-V2.1-Master": (
                VideoModelInputRequirement.image_required
            ),
            "pixverse-v5.6-i2v": VideoModelInputRequirement.image_required,
            "wan2.6-r2v": VideoModelInputRequirement.image_required,
            "Kling-Text2Video-V2.1-Master": VideoModelInputRequirement.text_only,
            "pixverse-v5.6-t2v": VideoModelInputRequirement.text_only,
            "pixverse-v5.6-transition": (
                VideoModelInputRequirement.unsupported_multi_image
            ),
            "Veo3-Fast": VideoModelInputRequirement.image_optional,
            "wan2.7-videoedit": VideoModelInputRequirement.video_required,
            "future-gmi-video-model": VideoModelInputRequirement.image_optional,
        }

        for model, expected in expectations.items():
            with self.subTest(model=model):
                self.assertEqual(video_model_input_requirement(model), expected)

    def test_classifies_image_and_video_source_media(self) -> None:
        self.assertEqual(
            video_source_media_kind("image/webp"),
            VideoSourceMediaKind.image,
        )
        self.assertEqual(
            video_source_media_kind("video/mp4"),
            VideoSourceMediaKind.video,
        )

    def test_video_sources_fail_closed_until_provider_routing_exists(self) -> None:
        with self.assertRaisesRegex(
            GenerationInputError,
            "does not support video source inputs",
        ):
            validate_video_input_assets(
                model="veo-3.1-fast-generate-001",
                input_assets=[source_video()],
            )

        with self.assertRaisesRegex(
            GenerationInputError,
            "backend provider routing is not enabled yet",
        ):
            validate_video_input_assets(
                model="wan2.7-videoedit",
                input_assets=[source_video()],
            )

    def test_video_to_video_mode_is_explicit_when_routing_is_enabled(self) -> None:
        with patch(
            "app.services.generation.VIDEO_TO_VIDEO_ROUTING_ENABLED_MODELS",
            frozenset({"wan2.7-videoedit"}),
        ):
            mode = validate_video_input_assets(
                model="wan2.7-videoedit",
                input_assets=[source_video()],
            )

        self.assertEqual(mode, VideoInputMode.video_to_video)

    def test_verified_video_edit_model_requires_a_video_source(self) -> None:
        invalid_inputs = ([], [source_image()])

        for input_assets in invalid_inputs:
            with self.subTest(input_assets=input_assets):
                with self.assertRaisesRegex(
                    GenerationInputError,
                    "requires one source video",
                ):
                    validate_video_input_assets(
                        model="wan2.7-videoedit",
                        input_assets=input_assets,
                    )

    def test_enforces_model_input_requirements(self) -> None:
        invalid_requests = (
            ("Kling-Image2Video-V2.1-Master", []),
            ("Kling-Text2Video-V2.1-Master", [source_image()]),
            ("pixverse-v5.6-transition", []),
        )

        for model, input_assets in invalid_requests:
            with self.subTest(model=model):
                with self.assertRaises(GenerationInputError):
                    validate_video_input_assets(
                        model=model,
                        input_assets=input_assets,
                    )

    def test_rejects_multiple_or_malformed_source_images(self) -> None:
        invalid_inputs = (
            [source_image(), source_image()],
            [source_image(role="brand_reference")],
            [source_image(url="http://example.com/product.jpg")],
            [source_image(content_type="image/gif", filename="product.gif")],
            [source_image(size_bytes=0)],
            [source_image(size_bytes=25 * 1024 * 1024 + 1)],
        )

        for input_assets in invalid_inputs:
            with self.subTest(input_assets=input_assets):
                with self.assertRaises(GenerationInputError):
                    validate_video_input_assets(
                        model="Veo3-Fast",
                        input_assets=input_assets,
                    )

    def test_infers_supported_type_from_filename(self) -> None:
        input_asset = source_image(
            content_type="application/octet-stream",
            filename="product.webp",
        )

        mode = validate_video_input_assets(
            model="Veo3-Fast",
            input_assets=[input_asset],
        )

        self.assertEqual(mode, VideoInputMode.image_to_video)

    def test_keeps_context_assets_out_of_provider_inputs(self) -> None:
        source = source_image()
        brand_context = {
            "role": "brand_reference",
            "storage_key": "brand-assets/guidelines.pdf",
            "filename": "guidelines.pdf",
            "content_type": "application/pdf",
            "size_bytes": 4096,
            "source": "campaign_brand_asset",
        }

        plan = build_video_input_plan(
            model="Veo3-Fast",
            source_input_assets=[source],
            context_assets=[brand_context],
        )

        self.assertEqual(plan.mode, VideoInputMode.image_to_video)
        self.assertEqual(plan.provider_input_assets, [source])
        self.assertEqual(plan.provenance_input_assets, [source, brand_context])


if __name__ == "__main__":
    unittest.main()
