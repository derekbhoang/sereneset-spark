import json
import re
import uuid
from collections.abc import Iterator
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


class StorageOperationError(RuntimeError):
    pass


class StorageObjectTooLargeError(StorageOperationError):
    pass


DEFAULT_DOWNLOAD_CHUNK_SIZE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class StoredObject:
    bucket: str
    key: str
    content_type: str
    size: int
    etag: str | None = None


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


def build_brand_asset_storage_key(
    *,
    brand_asset_id: uuid.UUID,
    filename: str,
) -> str:
    return normalize_storage_key(
        "/".join(
            [
                "brand-assets",
                str(brand_asset_id),
                "original",
                normalize_artifact_filename(filename),
            ]
        )
    )


def normalize_asset_version_input_role(role: str) -> str:
    normalized_role = role.strip().lower().replace(" ", "_")

    if not normalized_role:
        raise ValueError("Input role must not be empty")

    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", normalized_role):
        raise ValueError(
            "Input role must contain only lowercase letters, numbers, "
            "underscores, and hyphens"
        )

    return normalized_role


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


def build_asset_version_input_storage_key(
    *,
    campaign_id: uuid.UUID,
    asset_id: uuid.UUID,
    version_number: int,
    role: str,
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
                "inputs",
                normalize_asset_version_input_role(role),
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
    def __init__(
        self,
        settings: Settings,
        *,
        client_config: Config | None = None,
    ) -> None:
        self.settings = settings
        self.bucket_name = settings.b2_bucket_name
        self._client_config = client_config or Config(signature_version="s3v4")
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
                config=self._client_config,
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

    def check_bucket_access(self) -> None:
        self.client.head_bucket(Bucket=self.bucket_name)

    def get_object_info(self, *, key: str) -> StoredObject:
        storage_key = normalize_storage_key(key)
        response = self.client.head_object(
            Bucket=self.bucket_name,
            Key=storage_key,
        )
        size = response.get("ContentLength")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise StorageOperationError(
                "B2 did not return a valid object size"
            )

        raw_content_type = response.get("ContentType")
        content_type = (
            raw_content_type.strip()
            if isinstance(raw_content_type, str) and raw_content_type.strip()
            else "application/octet-stream"
        )
        raw_etag = response.get("ETag")
        etag = (
            raw_etag.strip()
            if isinstance(raw_etag, str) and raw_etag.strip()
            else None
        )

        return StoredObject(
            bucket=self.bucket_name,
            key=storage_key,
            content_type=content_type,
            size=size,
            etag=etag,
        )

    def copy_object(
        self,
        *,
        source_key: str,
        destination_key: str,
        content_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        cache_control: str | None = None,
        max_size_bytes: int | None = None,
    ) -> StoredObject:
        normalized_source_key = normalize_storage_key(source_key)
        normalized_destination_key = normalize_storage_key(destination_key)
        if max_size_bytes is not None and max_size_bytes < 1:
            raise ValueError("Maximum object size must be greater than zero")

        source_object = self.get_object_info(key=normalized_source_key)
        if source_object.size == 0:
            raise StorageOperationError("B2 source object is empty")

        if (
            max_size_bytes is not None
            and source_object.size > max_size_bytes
        ):
            raise StorageObjectTooLargeError(
                "B2 source object exceeds the configured size limit"
            )

        destination_content_type = (
            content_type.strip()
            if content_type is not None and content_type.strip()
            else source_object.content_type
        )
        copy_args: dict[str, Any] = {
            "Bucket": self.bucket_name,
            "Key": normalized_destination_key,
            "CopySource": {
                "Bucket": self.bucket_name,
                "Key": normalized_source_key,
            },
            "ContentType": destination_content_type,
            "Metadata": stringify_metadata(metadata),
            "MetadataDirective": "REPLACE",
        }
        if cache_control:
            copy_args["CacheControl"] = cache_control

        response = self.client.copy_object(**copy_args)
        copy_result = response.get("CopyObjectResult")
        raw_etag = (
            copy_result.get("ETag")
            if isinstance(copy_result, dict)
            else None
        )
        etag = (
            raw_etag.strip()
            if isinstance(raw_etag, str) and raw_etag.strip()
            else None
        )

        return StoredObject(
            bucket=self.bucket_name,
            key=normalized_destination_key,
            content_type=destination_content_type,
            size=source_object.size,
            etag=etag,
        )

    def delete_object(self, *, key: str) -> None:
        storage_key = normalize_storage_key(key)
        self.client.delete_object(Bucket=self.bucket_name, Key=storage_key)

    def download_bytes(self, *, key: str) -> bytes:
        storage_key = normalize_storage_key(key)
        response = self.client.get_object(Bucket=self.bucket_name, Key=storage_key)
        body = response["Body"]

        try:
            return body.read()
        finally:
            body.close()

    def iter_download_chunks(
        self,
        *,
        key: str,
        chunk_size_bytes: int = DEFAULT_DOWNLOAD_CHUNK_SIZE_BYTES,
        max_size_bytes: int | None = None,
    ) -> Iterator[bytes]:
        if chunk_size_bytes < 1:
            raise ValueError("Download chunk size must be greater than zero")

        if max_size_bytes is not None and max_size_bytes < 1:
            raise ValueError("Maximum object size must be greater than zero")

        storage_key = normalize_storage_key(key)
        response = self.client.get_object(Bucket=self.bucket_name, Key=storage_key)
        body = response["Body"]

        try:
            content_length = response.get("ContentLength")
            if (
                not isinstance(content_length, int)
                or isinstance(content_length, bool)
                or content_length < 0
            ):
                raise StorageOperationError(
                    "B2 did not return a valid object size"
                )

            if content_length == 0:
                raise StorageOperationError("B2 object is empty")

            if (
                max_size_bytes is not None
                and content_length > max_size_bytes
            ):
                raise StorageObjectTooLargeError(
                    "B2 object exceeds the configured size limit"
                )

            downloaded_bytes = 0
            while True:
                chunk = body.read(chunk_size_bytes)
                if not chunk:
                    break

                if not isinstance(chunk, bytes):
                    raise StorageOperationError(
                        "B2 returned a non-binary response body"
                    )

                downloaded_bytes += len(chunk)
                if (
                    max_size_bytes is not None
                    and downloaded_bytes > max_size_bytes
                ):
                    raise StorageObjectTooLargeError(
                        "B2 object exceeds the configured size limit"
                    )

                yield chunk

            if downloaded_bytes != content_length:
                raise StorageOperationError(
                    "B2 response size did not match its content length"
                )
        finally:
            body.close()


@lru_cache
def get_storage_service() -> B2StorageService:
    return B2StorageService(get_settings())


def get_readiness_storage_service() -> B2StorageService:
    settings = get_settings()
    timeout_seconds = settings.b2_readiness_timeout_seconds
    return B2StorageService(
        settings,
        client_config=Config(
            signature_version="s3v4",
            connect_timeout=timeout_seconds,
            read_timeout=timeout_seconds,
            retries={
                "mode": "standard",
                "total_max_attempts": 1,
            },
        ),
    )
