from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "SereneSet Spark"
    app_version: str = "1.0.0"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"

    database_url: str = Field(
        default="postgresql+psycopg://sereneset:sereneset@localhost:5432/sereneset_spark",
        alias="DATABASE_URL",
    )
    b2_endpoint_url: str = Field(
        default="https://s3.us-west-004.backblazeb2.com",
        alias="B2_ENDPOINT_URL",
    )
    b2_region_name: str = Field(default="us-west-004", alias="B2_REGION_NAME")
    b2_bucket_name: str = Field(default="", alias="B2_BUCKET_NAME")
    b2_application_key_id: str = Field(default="", alias="B2_APPLICATION_KEY_ID")
    b2_application_key: str = Field(default="", alias="B2_APPLICATION_KEY")
    genblaze_gmi_api_key: str = Field(default="", alias="GMI_API_KEY")
    genblaze_image_model: str = Field(
        default="seedream-5.0-lite",
        alias="GENBLAZE_IMAGE_MODEL",
    )
    genblaze_video_model: str = Field(
        default="Veo3-Fast",
        alias="GENBLAZE_VIDEO_MODEL",
    )
    genblaze_timeout_seconds: int = Field(
        default=600,
        alias="GENBLAZE_TIMEOUT_SECONDS",
        ge=30,
    )
    genblaze_video_timeout_seconds: int = Field(
        default=900,
        alias="GENBLAZE_VIDEO_TIMEOUT_SECONDS",
        ge=60,
    )
    max_generated_video_size_bytes: int = Field(
        default=500 * 1024 * 1024,
        alias="MAX_GENERATED_VIDEO_SIZE_BYTES",
        ge=1,
    )
    generation_worker_poll_seconds: float = Field(
        default=2.0,
        alias="GENERATION_WORKER_POLL_SECONDS",
        ge=0.1,
        le=60,
    )
    generation_job_stale_after_seconds: int = Field(
        default=1800,
        alias="GENERATION_JOB_STALE_AFTER_SECONDS",
        ge=60,
    )
    generation_job_max_attempts: int = Field(
        default=2,
        alias="GENERATION_JOB_MAX_ATTEMPTS",
        ge=1,
        le=10,
    )
    genblaze_storage_prefix: str = Field(
        default="sereneset-spark/genblaze",
        alias="GENBLAZE_STORAGE_PREFIX",
    )
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"],
        alias="CORS_ORIGINS",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("DATABASE_URL must not be empty")

        return value

    @field_validator("b2_endpoint_url")
    @classmethod
    def validate_b2_endpoint_url(cls, value: str) -> str:
        if value and not value.startswith("https://"):
            raise ValueError("B2_ENDPOINT_URL must start with https://")

        return value.rstrip("/")

    @field_validator("genblaze_video_model")
    @classmethod
    def validate_genblaze_video_model(cls, value: str) -> str:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("GENBLAZE_VIDEO_MODEL must not be empty")

        return normalized_value

    @field_validator("genblaze_storage_prefix")
    @classmethod
    def validate_genblaze_storage_prefix(cls, value: str) -> str:
        return value.strip().replace("\\", "/").strip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
