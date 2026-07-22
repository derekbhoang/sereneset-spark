import unittest

from app.services.generation import (
    VideoModelInputRequirement,
    VideoSourceMediaKind,
)
from app.services.video_model_capabilities import (
    VideoModelCapability,
    build_video_model_capability_registry,
    get_video_model_capability,
)


class VideoModelCapabilityRegistryTests(unittest.TestCase):
    def test_resolves_exact_model_ids_and_aliases_case_insensitively(self) -> None:
        canonical = get_video_model_capability("veo-3.1-fast-generate-001")
        alias = get_video_model_capability("  VEO3-FAST  ")

        self.assertIsNotNone(canonical)
        self.assertIs(alias, canonical)
        assert canonical is not None
        self.assertEqual(
            canonical.input_requirement,
            VideoModelInputRequirement.image_optional,
        )
        self.assertEqual(
            canonical.accepted_source_media_kinds,
            frozenset({VideoSourceMediaKind.image}),
        )
        self.assertEqual(canonical.allowed_duration_seconds, (4, 6, 8))
        self.assertEqual(
            dict(canonical.provider_parameter_aliases),
            {
                "duration": "durationSeconds",
                "aspect_ratio": "aspectRatio",
            },
        )

    def test_records_verified_video_edit_contract_separately_from_routing(self) -> None:
        capability = get_video_model_capability("wan2.7-videoedit")

        self.assertIsNotNone(capability)
        assert capability is not None
        self.assertEqual(
            capability.input_requirement,
            VideoModelInputRequirement.video_required,
        )
        self.assertEqual(
            capability.accepted_source_media_kinds,
            frozenset({VideoSourceMediaKind.video}),
        )
        self.assertEqual(capability.provider_source_parameter, "video")
        self.assertEqual(
            capability.provider_allowed_parameters,
            frozenset({"prompt", "video"}),
        )
        self.assertEqual(
            capability.provider_required_parameters,
            frozenset({"prompt", "video"}),
        )
        self.assertTrue(capability.provider_source_routing_implemented)

    def test_does_not_infer_capabilities_for_unknown_model_names(self) -> None:
        self.assertIsNone(get_video_model_capability("future-image2video-model"))

    def test_rejects_duplicate_model_names_and_aliases(self) -> None:
        capabilities = (
            VideoModelCapability(
                model_id="first-model",
                input_requirement=VideoModelInputRequirement.text_only,
            ),
            VideoModelCapability(
                model_id="second-model",
                aliases=("FIRST-MODEL",),
                input_requirement=VideoModelInputRequirement.text_only,
            ),
        )

        with self.assertRaisesRegex(ValueError, "Duplicate video model"):
            build_video_model_capability_registry(capabilities)

    def test_rejects_an_incomplete_provider_routing_contract(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "provider source parameter",
        ):
            VideoModelCapability(
                model_id="incomplete-video-edit",
                input_requirement=VideoModelInputRequirement.video_required,
                accepted_source_media_kinds=frozenset(
                    {VideoSourceMediaKind.video}
                ),
                provider_source_routing_implemented=True,
            )


if __name__ == "__main__":
    unittest.main()
