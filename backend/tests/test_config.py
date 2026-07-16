import unittest

from pydantic import ValidationError

from app.core.config import Settings


def make_production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "ENVIRONMENT": "production",
        "DATABASE_URL": (
            "postgresql+psycopg://user:password@db.example.com:5432/"
            "sereneset_spark?sslmode=require"
        ),
        "B2_ENDPOINT_URL": "https://s3.us-east-005.backblazeb2.com",
        "B2_REGION_NAME": "us-east-005",
        "B2_BUCKET_NAME": "sereneset-media",
        "B2_APPLICATION_KEY_ID": "production-key-id",
        "B2_APPLICATION_KEY": "production-application-key",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class ProductionServiceSettingsTests(unittest.TestCase):
    def test_normalizes_common_managed_postgresql_urls(self) -> None:
        for scheme in ("postgres://", "postgresql://"):
            with self.subTest(scheme=scheme):
                settings = Settings(
                    _env_file=None,
                    DATABASE_URL=(
                        f"{scheme}user:password@db.example.com:5432/"
                        "sereneset_spark?sslmode=require"
                    ),
                )

                self.assertTrue(
                    settings.database_url.startswith("postgresql+psycopg://")
                )

    def test_accepts_managed_postgresql_and_b2_in_production(self) -> None:
        settings = make_production_settings()

        self.assertEqual(settings.environment, "production")
        self.assertEqual(settings.database_pool_size, 3)
        self.assertEqual(settings.database_max_overflow, 2)
        self.assertEqual(settings.database_pool_timeout_seconds, 30)
        self.assertEqual(settings.database_pool_recycle_seconds, 300)
        self.assertEqual(settings.database_connect_timeout_seconds, 10)
        self.assertEqual(settings.b2_readiness_timeout_seconds, 5)

    def test_rejects_local_postgresql_in_production(self) -> None:
        with self.assertRaisesRegex(
            ValidationError,
            "must point to managed PostgreSQL",
        ):
            make_production_settings(
                DATABASE_URL=(
                    "postgresql+psycopg://user:password@localhost:5432/"
                    "sereneset_spark?sslmode=require"
                )
            )

    def test_rejects_postgresql_without_tls_in_production(self) -> None:
        with self.assertRaisesRegex(ValidationError, "must require TLS"):
            make_production_settings(
                DATABASE_URL=(
                    "postgresql+psycopg://user:password@db.example.com:5432/"
                    "sereneset_spark"
                )
            )

    def test_rejects_incomplete_b2_configuration_in_production(self) -> None:
        for overrides in (
            {"B2_BUCKET_NAME": ""},
            {"B2_APPLICATION_KEY_ID": "replace-me"},
            {"B2_APPLICATION_KEY": "changeme"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(
                    ValidationError,
                    "Production requires configured B2 settings",
                ):
                    make_production_settings(**overrides)


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
        self.assertEqual(settings.worker_heartbeat_interval_seconds, 10.0)
        self.assertEqual(settings.worker_heartbeat_stale_after_seconds, 45)
        self.assertEqual(settings.generation_job_stale_after_seconds, 1800)
        self.assertEqual(settings.generation_job_max_attempts, 2)

    def test_video_generation_environment_overrides(self) -> None:
        settings = Settings(
            _env_file=None,
            GENBLAZE_VIDEO_MODEL="  Kling-Text2Video-V2.1-Master  ",
            GENBLAZE_VIDEO_TIMEOUT_SECONDS=1200,
            MAX_GENERATED_VIDEO_SIZE_BYTES=100 * 1024 * 1024,
            GENERATION_WORKER_POLL_SECONDS=0.5,
            WORKER_HEARTBEAT_INTERVAL_SECONDS=15,
            WORKER_HEARTBEAT_STALE_AFTER_SECONDS=60,
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
        self.assertEqual(settings.worker_heartbeat_interval_seconds, 15)
        self.assertEqual(settings.worker_heartbeat_stale_after_seconds, 60)
        self.assertEqual(settings.generation_job_stale_after_seconds, 2400)
        self.assertEqual(settings.generation_job_max_attempts, 3)

    def test_rejects_invalid_video_generation_settings(self) -> None:
        invalid_settings = (
            {"GENBLAZE_VIDEO_MODEL": "   "},
            {"GENBLAZE_VIDEO_TIMEOUT_SECONDS": 59},
            {"MAX_GENERATED_VIDEO_SIZE_BYTES": 0},
            {"B2_READINESS_TIMEOUT_SECONDS": 0},
            {"GENERATION_WORKER_POLL_SECONDS": 0},
            {"WORKER_HEARTBEAT_INTERVAL_SECONDS": 0},
            {
                "WORKER_HEARTBEAT_INTERVAL_SECONDS": 10,
                "WORKER_HEARTBEAT_STALE_AFTER_SECONDS": 10,
            },
            {"GENERATION_JOB_STALE_AFTER_SECONDS": 59},
            {"GENERATION_JOB_MAX_ATTEMPTS": 0},
        )

        for overrides in invalid_settings:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    Settings(_env_file=None, **overrides)


if __name__ == "__main__":
    unittest.main()
