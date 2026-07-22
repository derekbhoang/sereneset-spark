import unittest

from scripts.check_gmi_video_model import (
    analyze_model_details,
    is_explicit_video_input_parameter,
    model_details_url,
)


class CheckGmiVideoModelTests(unittest.TestCase):
    def test_verifies_active_model_with_explicit_video_input(self) -> None:
        report = analyze_model_details(
            {
                "model": "wan2.7-videoedit",
                "status": "active",
                "model_type": "video",
                "brief_description": "Edit a source video.",
                "modalities": {
                    "input": [
                        {"type": "text", "required": True},
                        {"type": "video", "required": True},
                    ],
                    "output": [{"type": "video", "required": True}],
                },
                "parameters": [
                    {"name": "prompt", "type": "string", "required": True},
                    {"name": "video", "type": "video", "required": True},
                ],
            },
            requested_model="wan2.7-videoedit",
        )

        self.assertTrue(report.video_to_video_verified)
        self.assertEqual(report.video_input_parameters, ("video",))
        self.assertEqual(report.required_video_input_parameters, ("video",))

    def test_rejects_image_to_video_model(self) -> None:
        report = analyze_model_details(
            {
                "model": "veo-3.1-fast-generate-001",
                "status": "active",
                "model_type": "video",
                "modalities": {
                    "input": [
                        {"type": "text", "required": True},
                        {"type": "image", "required": False},
                    ],
                    "output": [{"type": "video", "required": True}],
                },
                "parameters": [
                    {"name": "prompt", "type": "string", "required": True},
                    {"name": "image", "type": "image", "required": False},
                ],
            },
            requested_model="veo-3.1-fast-generate-001",
        )

        self.assertFalse(report.video_to_video_verified)
        self.assertEqual(
            report.reason,
            "input modalities do not declare video",
        )

    def test_requires_modality_and_parameter_to_agree(self) -> None:
        report = analyze_model_details(
            {
                "model": "ambiguous-model",
                "status": "active",
                "model_type": "video",
                "modalities": {
                    "input": [{"type": "video", "required": True}],
                    "output": [{"type": "video", "required": True}],
                },
                "parameters": [
                    {"name": "duration", "type": "integer", "required": True}
                ],
            },
            requested_model="ambiguous-model",
        )

        self.assertFalse(report.video_to_video_verified)
        self.assertEqual(
            report.reason,
            "model parameters do not expose an explicit video input",
        )

    def test_recognizes_camel_case_video_url_parameter(self) -> None:
        self.assertTrue(
            is_explicit_video_input_parameter(
                {"name": "inputVideoUrl", "type": "string"}
            )
        )
        self.assertFalse(
            is_explicit_video_input_parameter(
                {"name": "videoLength", "type": "integer"}
            )
        )

    def test_encodes_model_slug_in_details_url(self) -> None:
        self.assertEqual(
            model_details_url(
                base_url="https://example.com/api/",
                model="model/with spaces",
            ),
            "https://example.com/api/models/model%2Fwith%20spaces",
        )


if __name__ == "__main__":
    unittest.main()
