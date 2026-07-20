import unittest
import uuid
from datetime import UTC, datetime

from pydantic import ValidationError

from app.models.generation_job import GenerationJob
from app.schemas.generation_job import (
    GenerationJobRead,
    VideoAspectRatio,
    VideoGenerationCreate,
    VideoResolution,
)


class VideoGenerationSchemaTests(unittest.TestCase):
    def test_video_request_uses_bounded_provider_neutral_defaults(self) -> None:
        request = VideoGenerationCreate(
            channel="  Paid social  ",
            prompt="  Slowly orbit around the product.  ",
        )

        self.assertEqual(request.channel, "Paid social")
        self.assertEqual(request.prompt, "Slowly orbit around the product.")
        self.assertEqual(request.duration_seconds, 4)
        self.assertEqual(request.aspect_ratio, VideoAspectRatio.landscape)
        self.assertEqual(request.resolution, VideoResolution.hd)
        self.assertIsNone(request.source_version_id)
        self.assertIsNone(request.source_brand_asset_id)

    def test_video_request_accepts_exactly_one_source_kind(self) -> None:
        brand_asset_id = uuid.uuid4()
        request = VideoGenerationCreate(
            channel="Paid social",
            prompt="Move only the background.",
            source_brand_asset_id=brand_asset_id,
        )

        self.assertEqual(request.source_brand_asset_id, brand_asset_id)
        self.assertIsNone(request.source_version_id)

        with self.assertRaises(ValidationError):
            VideoGenerationCreate(
                channel="Paid social",
                prompt="Move only the background.",
                source_version_id=uuid.uuid4(),
                source_brand_asset_id=brand_asset_id,
            )

    def test_video_request_rejects_invalid_or_unknown_controls(self) -> None:
        invalid_payloads = (
            {"channel": "Paid social", "prompt": "", "duration_seconds": 4},
            {"channel": "Paid social", "prompt": "Motion", "duration_seconds": 1},
            {"channel": "Paid social", "prompt": "Motion", "duration_seconds": 21},
            {"channel": "Paid social", "prompt": "Motion", "aspect_ratio": "4:3"},
            {"channel": "Paid social", "prompt": "Motion", "resolution": "4k"},
            {"channel": "Paid social", "prompt": "Motion", "format": "image"},
            {"channel": "Paid social", "prompt": "Motion", "status": "approved"},
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    VideoGenerationCreate.model_validate(payload)

    def test_job_read_schema_serializes_model_state(self) -> None:
        now = datetime.now(UTC)
        job = GenerationJob(
            id=uuid.uuid4(),
            asset_version_id=uuid.uuid4(),
            kind="video",
            status="queued",
            provider="gmicloud",
            model="Veo3-Fast",
            prompt="Slowly orbit around the product.",
            parameters={"duration": 4, "aspect_ratio": "16:9"},
            progress_percent=0,
            provider_job_id=None,
            attempt_count=0,
            error_message=None,
            started_at=None,
            completed_at=None,
            created_at=now,
            updated_at=now,
        )

        response = GenerationJobRead.model_validate(job)
        serialized = response.model_dump(mode="json")

        self.assertEqual(serialized["kind"], "video")
        self.assertEqual(serialized["status"], "queued")
        self.assertEqual(serialized["parameters"]["duration"], 4)

    def test_job_read_schema_rejects_invalid_progress(self) -> None:
        now = datetime.now(UTC)
        payload = {
            "id": uuid.uuid4(),
            "asset_version_id": uuid.uuid4(),
            "kind": "video",
            "status": "running",
            "provider": "gmicloud",
            "model": "Veo3-Fast",
            "prompt": "Motion",
            "parameters": {},
            "progress_percent": 101,
            "attempt_count": 1,
            "created_at": now,
            "updated_at": now,
        }

        with self.assertRaises(ValidationError):
            GenerationJobRead.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
