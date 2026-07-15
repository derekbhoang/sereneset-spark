import unittest

from pydantic import ValidationError

from app.core.config import Settings


class VideoGenerationSettingsTests(unittest.TestCase):
    def test_video_generation_defaults(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(
            settings.genblaze_video_model,
            "veo-3.1-fast-generate-001",
        )
        self.assertEqual(settings.genblaze_video_timeout_seconds, 900)
        self.assertEqual(
            settings.max_generated_video_size_bytes,
            500 * 1024 * 1024,
        )
        self.assertEqual(settings.generation_worker_poll_seconds, 2.0)
        self.assertEqual(settings.generation_job_stale_after_seconds, 1800)
        self.assertEqual(settings.generation_job_max_attempts, 2)

    def test_video_generation_environment_overrides(self) -> None:
        settings = Settings(
            _env_file=None,
            GENBLAZE_VIDEO_MODEL="  Kling-Text2Video-V2.1-Master  ",
            GENBLAZE_VIDEO_TIMEOUT_SECONDS=1200,
            MAX_GENERATED_VIDEO_SIZE_BYTES=100 * 1024 * 1024,
            GENERATION_WORKER_POLL_SECONDS=0.5,
            GENERATION_JOB_STALE_AFTER_SECONDS=2400,
            GENERATION_JOB_MAX_ATTEMPTS=3,
        )

        self.assertEqual(
            settings.genblaze_video_model,
            "Kling-Text2Video-V2.1-Master",
        )
        self.assertEqual(settings.genblaze_video_timeout_seconds, 1200)
        self.assertEqual(
            settings.max_generated_video_size_bytes,
            100 * 1024 * 1024,
        )
        self.assertEqual(settings.generation_worker_poll_seconds, 0.5)
        self.assertEqual(settings.generation_job_stale_after_seconds, 2400)
        self.assertEqual(settings.generation_job_max_attempts, 3)

    def test_rejects_invalid_video_generation_settings(self) -> None:
        invalid_settings = (
            {"GENBLAZE_VIDEO_MODEL": "   "},
            {"GENBLAZE_VIDEO_TIMEOUT_SECONDS": 59},
            {"MAX_GENERATED_VIDEO_SIZE_BYTES": 0},
            {"GENERATION_WORKER_POLL_SECONDS": 0},
            {"GENERATION_JOB_STALE_AFTER_SECONDS": 59},
            {"GENERATION_JOB_MAX_ATTEMPTS": 0},
        )

        for overrides in invalid_settings:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    Settings(_env_file=None, **overrides)


if __name__ == "__main__":
    unittest.main()
