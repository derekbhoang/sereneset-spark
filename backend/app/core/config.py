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
    genblaze_timeout_seconds: int = Field(
        default=600,
        alias="GENBLAZE_TIMEOUT_SECONDS",
        ge=30,
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

    @field_validator("genblaze_storage_prefix")
    @classmethod
    def validate_genblaze_storage_prefix(cls, value: str) -> str:
        return value.strip().replace("\\", "/").strip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
