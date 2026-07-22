from __future__ import annotations

import uuid
from typing import Any

from app.models.asset import (
    AssetInputMediaKind,
    AssetVersionInput,
)


DOCUMENT_CONTENT_TYPES = frozenset(
    {
        "application/msword",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)


def infer_input_media_kind(content_type: str) -> AssetInputMediaKind:
    normalized_content_type = content_type.split(";", maxsplit=1)[0].strip().lower()
    if normalized_content_type.startswith("image/"):
        return AssetInputMediaKind.image
    if normalized_content_type.startswith("video/"):
        return AssetInputMediaKind.video
    if (
        normalized_content_type.startswith("text/")
        or normalized_content_type in DOCUMENT_CONTENT_TYPES
    ):
        return AssetInputMediaKind.document

    return AssetInputMediaKind.other


def optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()

    return None


def required_string(record: dict[str, Any], key: str) -> str:
    value = optional_string(record.get(key))
    if value is None:
        raise ValueError(f"Input provenance field '{key}' must not be empty")

    return value


def optional_uuid(value: object, *, field_name: str) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value

    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(
            f"Input provenance field '{field_name}' must be a UUID"
        ) from exc


def optional_positive_int(value: object, *, field_name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or (
        isinstance(value, float) and not value.is_integer()
    ):
        raise ValueError(
            f"Input provenance field '{field_name}' must be a positive integer"
        )

    try:
        parsed_value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Input provenance field '{field_name}' must be a positive integer"
        ) from exc
    if parsed_value < 1:
        raise ValueError(
            f"Input provenance field '{field_name}' must be a positive integer"
        )

    return parsed_value


def build_asset_version_input(
    *,
    asset_version_id: uuid.UUID,
    record: dict[str, Any],
) -> AssetVersionInput:
    content_type = required_string(record, "content_type")
    configured_media_kind = optional_string(record.get("media_kind"))
    try:
        media_kind = (
            AssetInputMediaKind(configured_media_kind)
            if configured_media_kind is not None
            else infer_input_media_kind(content_type)
        )
    except ValueError as exc:
        raise ValueError("Input provenance field 'media_kind' is invalid") from exc

    size_bytes = optional_positive_int(
        record.get("size_bytes"),
        field_name="size_bytes",
    )
    if size_bytes is None:
        raise ValueError("Input provenance field 'size_bytes' must not be empty")

    source_asset_id = optional_uuid(
        record.get("source_asset_id"),
        field_name="source_asset_id",
    )
    source_version_id = optional_uuid(
        record.get("source_version_id"),
        field_name="source_version_id",
    )
    source_version_number = optional_positive_int(
        record.get("source_version_number"),
        field_name="source_version_number",
    )
    source_snapshot = (
        source_asset_id,
        source_version_id,
        source_version_number,
    )
    if any(value is not None for value in source_snapshot) and not all(
        value is not None for value in source_snapshot
    ):
        raise ValueError(
            "Source-version provenance requires source_asset_id, "
            "source_version_id, and source_version_number"
        )

    return AssetVersionInput(
        asset_version_id=asset_version_id,
        role=required_string(record, "role"),
        storage_key=required_string(record, "storage_key"),
        filename=required_string(record, "filename"),
        content_type=content_type,
        media_kind=media_kind.value,
        size_bytes=size_bytes,
        sha256=optional_string(record.get("sha256")),
        source=optional_string(record.get("source")) or "user_upload",
        storage_ownership=(
            optional_string(record.get("storage_ownership")) or "asset_version"
        ),
        source_asset_id=source_asset_id,
        source_version_id=source_version_id,
        source_version_number=source_version_number,
        brand_asset_id=optional_uuid(
            record.get("brand_asset_id"),
            field_name="brand_asset_id",
        ),
        campaign_brand_asset_id=optional_uuid(
            record.get("campaign_brand_asset_id"),
            field_name="campaign_brand_asset_id",
        ),
        brand_asset_type=optional_string(record.get("brand_asset_type")),
        brand_asset_name=optional_string(record.get("brand_asset_name")),
        usage_guidance=optional_string(record.get("usage_guidance")),
    )
