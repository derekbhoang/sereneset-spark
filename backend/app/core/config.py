from functools import lru_cache
from ipaddress import ip_address
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    app_name: str = "SereneSet Spark"
    app_version: str = "1.0.0"
    environment: str = Field(default="development", alias="ENVIRONMENT")
    api_v1_prefix: str = "/api/v1"

    database_url: str = Field(
        default="postgresql+psycopg://sereneset:sereneset@localhost:5432/sereneset_spark",
        alias="DATABASE_URL",
    )
    database_connection_mode: Literal["tls", "private"] = Field(
        default="tls",
        alias="DATABASE_CONNECTION_MODE",
    )
    database_pool_size: int = Field(
        default=3,
        alias="DATABASE_POOL_SIZE",
        ge=1,
        le=50,
    )
    database_max_overflow: int = Field(
        default=2,
        alias="DATABASE_MAX_OVERFLOW",
        ge=0,
        le=100,
    )
    database_pool_timeout_seconds: int = Field(
        default=30,
        alias="DATABASE_POOL_TIMEOUT_SECONDS",
        ge=1,
        le=300,
    )
    database_pool_recycle_seconds: int = Field(
        default=300,
        alias="DATABASE_POOL_RECYCLE_SECONDS",
        ge=60,
        le=3600,
    )
    database_connect_timeout_seconds: int = Field(
        default=10,
        alias="DATABASE_CONNECT_TIMEOUT_SECONDS",
        ge=1,
        le=60,
    )
    b2_endpoint_url: str = Field(
        default="https://s3.us-west-004.backblazeb2.com",
        alias="B2_ENDPOINT_URL",
    )
    b2_region_name: str = Field(default="us-west-004", alias="B2_REGION_NAME")
    b2_bucket_name: str = Field(default="", alias="B2_BUCKET_NAME")
    b2_application_key_id: str = Field(default="", alias="B2_APPLICATION_KEY_ID")
    b2_application_key: str = Field(default="", alias="B2_APPLICATION_KEY")
    b2_readiness_timeout_seconds: int = Field(
        default=5,
        alias="B2_READINESS_TIMEOUT_SECONDS",
        ge=1,
        le=30,
    )
    genblaze_gmi_api_key: str = Field(default="", alias="GMI_API_KEY")
    genblaze_image_model: str = Field(
        default="seedream-5.0-lite",
        alias="GENBLAZE_IMAGE_MODEL",
    )
    genblaze_video_model: str = Field(
        default="veo-3.1-fast-generate-001",
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
    worker_heartbeat_interval_seconds: float = Field(
        default=10.0,
        alias="WORKER_HEARTBEAT_INTERVAL_SECONDS",
        ge=1,
        le=300,
    )
    worker_heartbeat_stale_after_seconds: int = Field(
        default=45,
        alias="WORKER_HEARTBEAT_STALE_AFTER_SECONDS",
        ge=5,
        le=3600,
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
    public_frontend_url: str = Field(default="", alias="PUBLIC_FRONTEND_URL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("DATABASE_URL must not be empty")

        normalized_scheme = normalized_value.casefold()
        if normalized_scheme.startswith("postgres://"):
            normalized_value = (
                "postgresql+psycopg://" + normalized_value[len("postgres://") :]
            )
        elif normalized_scheme.startswith("postgresql://"):
            normalized_value = (
                "postgresql+psycopg://" + normalized_value[len("postgresql://") :]
            )

        try:
            database_url = make_url(normalized_value)
        except ArgumentError as exc:
            raise ValueError("DATABASE_URL must be a valid SQLAlchemy URL") from exc

        if database_url.drivername != "postgresql+psycopg":
            raise ValueError("DATABASE_URL must use PostgreSQL with the psycopg driver")

        return normalized_value

    @field_validator("environment")
    @classmethod
    def normalize_environment(cls, value: str) -> str:
        normalized_value = value.strip().casefold()
        if not normalized_value:
            raise ValueError("ENVIRONMENT must not be empty")

        return normalized_value

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

    @field_validator("public_frontend_url")
    @classmethod
    def normalize_public_frontend_url(cls, value: str) -> str:
        normalized_value = value.strip().rstrip("/")
        if normalized_value and not normalized_value.startswith(
            ("http://", "https://")
        ):
            raise ValueError("PUBLIC_FRONTEND_URL must be an HTTP(S) URL")

        return normalized_value

    @property
    def allowed_cors_origins(self) -> list[str]:
        origins = list(self.cors_origins)
        if self.public_frontend_url and self.public_frontend_url not in origins:
            origins.append(self.public_frontend_url)

        return origins

    @model_validator(mode="after")
    def validate_worker_heartbeat_window(self) -> Self:
        if (
            self.worker_heartbeat_stale_after_seconds
            <= self.worker_heartbeat_interval_seconds
        ):
            raise ValueError(
                "WORKER_HEARTBEAT_STALE_AFTER_SECONDS must be greater than "
                "WORKER_HEARTBEAT_INTERVAL_SECONDS"
            )

        return self

    @model_validator(mode="after")
    def validate_production_services(self) -> Self:
        if self.environment != "production":
            return self

        if self.public_frontend_url and not self.public_frontend_url.startswith(
            "https://"
        ):
            raise ValueError("Production PUBLIC_FRONTEND_URL must use HTTPS")

        database_url = make_url(self.database_url)
        hostname = (database_url.host or "").rstrip(".").casefold()
        local_hostnames = {
            "0.0.0.0",
            "host.docker.internal",
            "localhost",
            "localhost.localdomain",
            "postgres",
        }
        is_loopback = False
        try:
            is_loopback = ip_address(hostname).is_loopback
        except ValueError:
            pass

        if not hostname or hostname in local_hostnames or is_loopback:
            raise ValueError("Production DATABASE_URL must point to managed PostgreSQL")

        if self.database_connection_mode == "private":
            try:
                ip_address(hostname)
            except ValueError:
                pass
            else:
                raise ValueError(
                    "Private production DATABASE_URL must use an internal DNS hostname"
                )

            if "." in hostname:
                raise ValueError(
                    "Private production DATABASE_URL must use an internal DNS hostname"
                )
        else:
            sslmode = database_url.query.get("sslmode")
            allowed_ssl_modes = {"require", "verify-ca", "verify-full"}
            if (
                not isinstance(sslmode, str)
                or sslmode.casefold() not in allowed_ssl_modes
            ):
                raise ValueError(
                    "Production DATABASE_URL must require TLS with "
                    "sslmode=require, verify-ca, or verify-full"
                )

        missing_b2_settings = [
            name
            for name, value in {
                "B2_ENDPOINT_URL": self.b2_endpoint_url,
                "B2_REGION_NAME": self.b2_region_name,
                "B2_BUCKET_NAME": self.b2_bucket_name,
                "B2_APPLICATION_KEY_ID": self.b2_application_key_id,
                "B2_APPLICATION_KEY": self.b2_application_key,
            }.items()
            if not value.strip()
            or value.strip().casefold() in {"changeme", "replace-me"}
        ]
        if missing_b2_settings:
            raise ValueError(
                "Production requires configured B2 settings: "
                + ", ".join(missing_b2_settings)
            )

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
