import json
import re
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.config import Config

from app.core.config import Settings, get_settings


class StorageConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredObject:
    bucket: str
    key: str
    content_type: str
    size: int


def normalize_storage_key(key: str) -> str:
    normalized_key = key.strip().replace("\\", "/").lstrip("/")

    if not normalized_key:
        raise ValueError("Storage key must not be empty")

    if any(part == ".." for part in normalized_key.split("/")):
        raise ValueError("Storage key must not contain parent directory segments")

    return normalized_key


def build_asset_version_storage_key(
    *,
    campaign_id: uuid.UUID,
    asset_id: uuid.UUID,
    version_number: int,
    filename: str = "metadata.json",
) -> str:
    if version_number < 1:
        raise ValueError("Version number must be greater than zero")

    return normalize_storage_key(
        "/".join(
            [
                "campaigns",
                str(campaign_id),
                "assets",
                str(asset_id),
                "versions",
                f"v{version_number}",
                filename,
            ]
        )
    )


def normalize_artifact_filename(filename: str) -> str:
    leaf_filename = PurePosixPath(filename.strip().replace("\\", "/")).name.strip()

    if not leaf_filename:
        raise ValueError("Artifact filename must not be empty")

    safe_filename = re.sub(r"[^A-Za-z0-9._-]+", "-", leaf_filename).strip(".-")

    if not safe_filename:
        raise ValueError("Artifact filename must contain a safe name")

    return safe_filename[:240]


def build_asset_version_artifact_storage_key(
    *,
    campaign_id: uuid.UUID,
    asset_id: uuid.UUID,
    version_number: int,
    filename: str,
) -> str:
    if version_number < 1:
        raise ValueError("Version number must be greater than zero")

    return normalize_storage_key(
        "/".join(
            [
                "campaigns",
                str(campaign_id),
                "assets",
                str(asset_id),
                "versions",
                f"v{version_number}",
                "artifact",
                normalize_artifact_filename(filename),
            ]
        )
    )


def stringify_metadata(metadata: dict[str, Any] | None) -> dict[str, str]:
    if not metadata:
        return {}

    return {
        str(key): json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        for key, value in metadata.items()
        if value is not None
    }


class B2StorageService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bucket_name = settings.b2_bucket_name
        self._client: BaseClient | None = None

    def _validate_settings(self) -> None:
        missing_settings = [
            name
            for name, value in {
                "B2_BUCKET_NAME": self.settings.b2_bucket_name,
                "B2_APPLICATION_KEY_ID": self.settings.b2_application_key_id,
                "B2_APPLICATION_KEY": self.settings.b2_application_key,
                "B2_ENDPOINT_URL": self.settings.b2_endpoint_url,
            }.items()
            if not value
        ]

        if missing_settings:
            joined_settings = ", ".join(missing_settings)
            raise StorageConfigurationError(
                f"Missing required B2 settings: {joined_settings}"
            )

    @property
    def client(self) -> BaseClient:
        self._validate_settings()

        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.settings.b2_endpoint_url,
                region_name=self.settings.b2_region_name,
                aws_access_key_id=self.settings.b2_application_key_id,
                aws_secret_access_key=self.settings.b2_application_key,
                config=Config(signature_version="s3v4"),
            )

        return self._client

    def upload_bytes(
        self,
        *,
        key: str,
        body: bytes,
        content_type: str,
        metadata: dict[str, Any] | None = None,
        cache_control: str | None = None,
    ) -> StoredObject:
        storage_key = normalize_storage_key(key)

        put_object_args: dict[str, Any] = {
            "Bucket": self.bucket_name,
            "Key": storage_key,
            "Body": body,
            "ContentType": content_type,
            "Metadata": stringify_metadata(metadata),
        }

        if cache_control:
            put_object_args["CacheControl"] = cache_control

        self.client.put_object(**put_object_args)

        return StoredObject(
            bucket=self.bucket_name,
            key=storage_key,
            content_type=content_type,
            size=len(body),
        )

    def upload_json(
        self,
        *,
        key: str,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> StoredObject:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )

        return self.upload_bytes(
            key=key,
            body=body,
            content_type="application/json",
            metadata=metadata,
        )

    def generate_presigned_download_url(
        self,
        *,
        key: str,
        expires_seconds: int = 3600,
    ) -> str:
        storage_key = normalize_storage_key(key)

        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket_name, "Key": storage_key},
            ExpiresIn=expires_seconds,
        )

    def delete_object(self, *, key: str) -> None:
        storage_key = normalize_storage_key(key)
        self.client.delete_object(Bucket=self.bucket_name, Key=storage_key)


@lru_cache
def get_storage_service() -> B2StorageService:
    return B2StorageService(get_settings())
