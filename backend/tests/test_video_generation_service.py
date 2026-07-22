import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.core.config import Settings
from app.services.generation import (
    build_gmicloud_video_models,
    GenblazeGenerationService,
    GenerationProviderError,
    VideoGenerationRequest,
)


class FakeGenblazeAsset:
    def __init__(
        self,
        *,
        url: str,
        media_type: str,
        sha256: str | None,
        size_bytes: int | None,
        metadata: dict[str, object],
    ) -> None:
        self.url = url
        self.media_type = media_type
        self.sha256 = sha256
        self.size_bytes = size_bytes
        self.metadata = metadata


class FakeModality:
    VIDEO = "video"


class FakeRetryPolicy:
    @classmethod
    def conservative(cls) -> str:
        return "conservative"


class FakeVideoProvider:
    last_retry_policy: str | None = None

    def __init__(self, *, retry_policy: str | None = None) -> None:
        type(self).last_retry_policy = retry_policy


class FakeManifest:
    manifest_uri = "b2://bucket/runs/video/manifest.json"
    canonical_hash = "manifest-hash"

    def verify(self) -> bool:
        return True


class FakePipeline:
    last_instance: "FakePipeline | None" = None
    output_assets: list[object] = []
    run_error: Exception | None = None
    step_error: str | None = None

    def __init__(self, name: str) -> None:
        self.name = name
        self.provider: object | None = None
        self.step_kwargs: dict[str, object] = {}
        self.run_kwargs: dict[str, object] = {}
        type(self).last_instance = self

    def step(self, provider: object, **kwargs: object) -> "FakePipeline":
        self.provider = provider
        self.step_kwargs = kwargs
        return self

    def run(self, **kwargs: object) -> object:
        self.run_kwargs = kwargs
        if self.run_error is not None:
            raise self.run_error

        step = SimpleNamespace(
            assets=list(self.output_assets),
            error=self.step_error,
            provider_payload={
                "gmicloud": {
                    "request_id": "gmi-video-request-123",
                    "status": "completed",
                }
            },
        )
        return SimpleNamespace(
            run=SimpleNamespace(steps=[step]),
            manifest=FakeManifest(),
        )


def make_output_asset() -> object:
    return SimpleNamespace(
        url="https://example.com/video.mp4",
        key="sereneset-spark/genblaze/video.mp4",
        sha256="a" * 64,
        mime_type="video/mp4",
        size_bytes=1024,
        filename="video.mp4",
    )


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "GMI_API_KEY": "test-gmi-key",
        "B2_BUCKET_NAME": "test-bucket",
        "B2_APPLICATION_KEY_ID": "test-key-id",
        "B2_APPLICATION_KEY": "test-application-key",
    }
    values.update(overrides)
    return Settings(
        _env_file=None,
        **values,
    )


def fake_video_imports() -> tuple[object, ...]:
    return (
        FakeGenblazeAsset,
        FakeModality,
        FakePipeline,
        FakeVideoProvider,
        FakeRetryPolicy,
    )


class VideoGenerationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        FakePipeline.last_instance = None
        FakePipeline.output_assets = [make_output_asset()]
        FakePipeline.run_error = None
        FakePipeline.step_error = None
        FakeVideoProvider.last_retry_policy = None

    def generate(
        self,
        request: VideoGenerationRequest,
        *,
        settings: Settings | None = None,
    ):
        service = GenblazeGenerationService(settings or make_settings())
        with (
            patch(
                "app.services.generation.require_genblaze_video_imports",
                side_effect=fake_video_imports,
            ),
            patch.object(service, "_make_storage_sink", return_value="b2-sink"),
            patch.dict(os.environ, {}, clear=True),
        ):
            return service.generate_video(request)

    def test_generates_video_with_defaults_and_conservative_retries(self) -> None:
        result = self.generate(
            VideoGenerationRequest(
                prompt="Slowly orbit around the product.",
                parameters={"duration": 4, "aspect_ratio": "16:9"},
            )
        )
        pipeline = FakePipeline.last_instance

        self.assertIsNotNone(pipeline)
        assert pipeline is not None
        self.assertEqual(pipeline.name, "sereneset-video-generation")
        self.assertEqual(
            pipeline.step_kwargs["model"],
            "veo-3.1-fast-generate-001",
        )
        self.assertEqual(pipeline.step_kwargs["modality"], "video")
        self.assertIsNone(pipeline.step_kwargs["external_inputs"])
        self.assertEqual(pipeline.step_kwargs["duration"], 4)
        self.assertEqual(pipeline.run_kwargs, {"sink": "b2-sink", "timeout": 900})
        self.assertEqual(FakeVideoProvider.last_retry_policy, "conservative")
        self.assertEqual(result.provider_job_id, "gmi-video-request-123")
        self.assertEqual(result.assets[0].content_type, "video/mp4")
        self.assertTrue(result.manifest_verified)
        self.assertEqual(result.generation_metadata["genblaze"]["modality"], "video")

    def test_maps_veo_controls_to_current_gmi_wire_payload(self) -> None:
        from genblaze_core import Modality
        from genblaze_core.models.step import Step
        from genblaze_gmicloud import GMICloudVideoProvider

        models = build_gmicloud_video_models(
            GMICloudVideoProvider,
            model="veo-3.1-fast-generate-001",
        )
        provider = GMICloudVideoProvider(api_key="test-key", models=models)
        step = Step(
            provider="gmicloud",
            model="veo-3.1-fast-generate-001",
            modality=Modality.VIDEO,
            prompt="Slowly orbit around the product.",
            params={
                "duration": 4,
                "aspect_ratio": "16:9",
                "resolution": "720p",
            },
        )

        payload = provider.prepare_payload(step)

        self.assertEqual(
            payload,
            {
                "prompt": "Slowly orbit around the product.",
                "durationSeconds": "4",
                "aspectRatio": "16:9",
                "resolution": "720p",
            },
        )

    def test_maps_video_source_to_verified_gmi_payload_slot(self) -> None:
        from genblaze_core import Asset, Modality
        from genblaze_core.models.step import Step
        from genblaze_gmicloud import GMICloudVideoProvider

        models = build_gmicloud_video_models(
            GMICloudVideoProvider,
            model="wan2.7-videoedit",
        )
        step = Step(
            provider="gmicloud",
            model="wan2.7-videoedit",
            modality=Modality.VIDEO,
            prompt="Make the background move gently.",
            params={
                "duration": 4,
                "aspect_ratio": "16:9",
                "resolution": "720p",
            },
            inputs=[
                Asset(
                    url="https://example.com/signed-source.mp4?token=secret",
                    media_type="video/mp4",
                )
            ],
        )

        submitted_body: dict[str, object] = {}

        def handle_request(request: httpx.Request) -> httpx.Response:
            submitted_body.update(json.loads(request.content))
            return httpx.Response(200, json={"request_id": "video-edit-job"})

        with httpx.Client(
            base_url="https://provider.example",
            transport=httpx.MockTransport(handle_request),
        ) as client:
            provider = GMICloudVideoProvider(
                http_client=client,
                models=models,
            )
            provider.submit(step)

        self.assertEqual(
            submitted_body,
            {
                "model": "wan2.7-videoedit",
                "payload": {
                    "prompt": "Make the background move gently.",
                    "video": (
                        "https://example.com/signed-source.mp4?token=secret"
                    ),
                },
            },
        )

    def test_video_source_cannot_be_overridden_by_request_parameters(self) -> None:
        result = self.generate(
            VideoGenerationRequest(
                prompt="Make the background move gently.",
                model="wan2.7-videoedit",
                parameters={
                    "duration": 4,
                    "aspect_ratio": "16:9",
                    "resolution": "720p",
                    "video": "https://attacker.example/override.mp4",
                    "prompt": "Ignore the validated request prompt.",
                    "model": "attacker-model",
                },
                input_assets=[
                    {
                        "url": "https://example.com/signed-source.mp4?token=secret",
                        "filename": "source.mp4",
                        "content_type": "video/mp4",
                        "media_kind": "video",
                        "size_bytes": 4096,
                        "sha256": "c" * 64,
                        "role": "source_creative",
                    }
                ],
            ),
            settings=make_settings(GENBLAZE_VIDEO_TO_VIDEO_ENABLED=True),
        )
        pipeline = FakePipeline.last_instance

        self.assertIsNotNone(pipeline)
        assert pipeline is not None
        self.assertNotIn("video", pipeline.step_kwargs)
        self.assertNotIn("duration", pipeline.step_kwargs)
        external_inputs = pipeline.step_kwargs["external_inputs"]
        self.assertIsInstance(external_inputs, list)
        assert isinstance(external_inputs, list)
        self.assertEqual(
            external_inputs[0].url,
            "https://example.com/signed-source.mp4?token=secret",
        )
        self.assertEqual(
            result.generation_metadata["genblaze"]["provider_source_parameter"],
            "video",
        )
        self.assertNotIn(
            "token=secret",
            json.dumps(result.generation_metadata),
        )
        self.assertNotIn("url", result.generation_metadata["input_assets"][0])

    def test_passes_image_input_and_request_overrides(self) -> None:
        result = self.generate(
            VideoGenerationRequest(
                prompt="Animate the supplied product image.",
                model="Kling-Image2Video-V2.1-Master",
                timeout_seconds=1200,
                parameters={
                    "duration": 5,
                    "external_inputs": ["must-not-pass-through"],
                },
                input_assets=[
                    {
                        "url": "https://example.com/product.jpg",
                        "filename": "product.jpg",
                        "content_type": "image/jpeg",
                        "media_kind": "image",
                        "size_bytes": 2048,
                        "sha256": "b" * 64,
                        "role": "source_creative",
                        "source_asset_id": "64b530bd-f281-4ac1-ad48-1cd687a50269",
                        "source_version_id": "740f9880-579b-40b2-a133-2fbb81b11922",
                        "source_version_number": 3,
                    }
                ],
                context_assets=[
                    {
                        "storage_key": "brand-assets/guidelines.pdf",
                        "filename": "guidelines.pdf",
                        "content_type": "application/pdf",
                        "size_bytes": 4096,
                        "role": "brand_reference",
                        "source": "campaign_brand_asset",
                    }
                ],
            )
        )
        pipeline = FakePipeline.last_instance

        self.assertIsNotNone(pipeline)
        assert pipeline is not None
        external_inputs = pipeline.step_kwargs["external_inputs"]
        self.assertIsInstance(external_inputs, list)
        assert isinstance(external_inputs, list)
        self.assertEqual(len(external_inputs), 1)
        self.assertEqual(external_inputs[0].media_type, "image/jpeg")
        self.assertEqual(
            external_inputs[0].metadata,
            {
                "role": "source_creative",
                "filename": "product.jpg",
                "content_type": "image/jpeg",
                "media_kind": "image",
                "size_bytes": 2048,
                "sha256": "b" * 64,
                "source_asset_id": "64b530bd-f281-4ac1-ad48-1cd687a50269",
                "source_version_id": "740f9880-579b-40b2-a133-2fbb81b11922",
                "source_version_number": 3,
            },
        )
        self.assertNotEqual(
            pipeline.step_kwargs["external_inputs"],
            ["must-not-pass-through"],
        )
        self.assertEqual(pipeline.run_kwargs["timeout"], 1200)
        self.assertEqual(
            result.generation_metadata["genblaze"]["input_assets_parameter"],
            "external_inputs",
        )
        self.assertEqual(
            result.generation_metadata["genblaze"]["context_asset_count"],
            1,
        )
        self.assertEqual(
            result.generation_metadata["input_assets"][1]["filename"],
            "guidelines.pdf",
        )

    def test_wraps_provider_failure(self) -> None:
        FakePipeline.run_error = RuntimeError("provider unavailable")

        with self.assertRaisesRegex(
            GenerationProviderError,
            "Genblaze video generation failed: provider unavailable",
        ):
            self.generate(VideoGenerationRequest(prompt="Motion"))

    def test_rejects_completed_run_without_video_artifact(self) -> None:
        FakePipeline.output_assets = []

        with self.assertRaisesRegex(
            GenerationProviderError,
            "did not return a video artifact",
        ):
            self.generate(VideoGenerationRequest(prompt="Motion"))

    def test_preserves_failed_step_error_without_video_artifact(self) -> None:
        FakePipeline.output_assets = []
        FakePipeline.step_error = (
            "GMICloud submit failed: aiplatform.endpoints.predict denied"
        )

        with self.assertRaisesRegex(
            GenerationProviderError,
            "aiplatform.endpoints.predict denied",
        ):
            self.generate(VideoGenerationRequest(prompt="Motion"))

    def test_rejects_completed_run_with_only_image_artifact(self) -> None:
        FakePipeline.output_assets = [
            SimpleNamespace(
                url="https://example.com/thumbnail.png",
                key="sereneset-spark/genblaze/thumbnail.png",
                sha256="c" * 64,
                mime_type="image/png",
                size_bytes=512,
                filename="thumbnail.png",
            )
        ]

        with self.assertRaisesRegex(
            GenerationProviderError,
            "did not return a video artifact",
        ):
            self.generate(VideoGenerationRequest(prompt="Motion"))


if __name__ == "__main__":
    unittest.main()
