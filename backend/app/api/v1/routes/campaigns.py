import hashlib
import json
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from ipaddress import ip_address
from io import BytesIO
from pathlib import Path, PurePosixPath
from shutil import copyfileobj
from tempfile import NamedTemporaryFile, SpooledTemporaryFile
from typing import BinaryIO
from urllib.error import URLError
from urllib.parse import ParseResult, unquote, urlparse
from urllib.request import Request, urlopen
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from starlette.background import BackgroundTask

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models.asset import (
    Asset,
    AssetFormat,
    AssetVersion,
    AssetVersionInput,
    ReviewStatus,
)
from app.models.brand_asset import BrandAsset, CampaignBrandAsset
from app.models.campaign import Campaign
from app.schemas.campaign import CampaignCreate, CampaignRead, CampaignUpdate
from app.services.storage import (
    B2StorageService,
    StorageConfigurationError,
    StorageObjectTooLargeError,
    StorageOperationError,
    get_storage_service,
    normalize_artifact_filename,
)
from app.services.video_refinement import (
    VIDEO_REFINEMENT_CONTRACT,
    VideoGenerationOperation,
)


router = APIRouter(prefix="/campaigns", tags=["campaigns"])
MAX_EXPORT_ARTIFACT_SIZE_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_EXPORTED_VIDEO_SIZE_BYTES = 500 * 1024 * 1024
DEFAULT_MAX_EXPORTED_VIDEO_INPUT_SIZE_BYTES = 100 * 1024 * 1024
MAX_EXPORT_METADATA_SIZE_BYTES = 2 * 1024 * 1024
EXPORT_STREAM_CHUNK_SIZE_BYTES = 1024 * 1024
EXPORT_SPOOL_MEMORY_LIMIT_BYTES = 1024 * 1024
VIDEO_ARTIFACT_SUFFIXES = {".m4v", ".mov", ".mp4", ".webm"}
EPHEMERAL_EXPORT_URL_KEYS = frozenset(
    {"url", "download_url", "presigned_url", "signed_url"}
)


@dataclass(frozen=True)
class ExportArtifactReference:
    storage_key: str | None
    url: str | None
    filename: str | None
    content_type: str | None
    size_bytes: int | None
    sha256: str | None
    source: str


@dataclass(frozen=True)
class ExportInputReference:
    id: uuid.UUID | None
    role: str | None
    storage_key: str | None
    url: str | None
    filename: str | None
    content_type: str | None
    media_kind: str | None
    size_bytes: int | None
    sha256: str | None
    created_at: str | None
    source: str
    input_source: str | None = None
    storage_ownership: str | None = None
    source_asset_id: str | None = None
    source_version_id: str | None = None
    source_version_number: int | None = None
    brand_asset_id: str | None = None
    campaign_brand_asset_id: str | None = None
    brand_asset_type: str | None = None
    brand_asset_name: str | None = None
    usage_guidance: str | None = None
    content_validation: dict[str, object] | None = None


@dataclass(frozen=True)
class ExportedFileResult:
    size_bytes: int
    sha256: str
    size_verified: bool | None
    sha256_verified: bool | None

    @property
    def integrity_verified(self) -> bool:
        return self.size_verified is True and self.sha256_verified is True


@dataclass(frozen=True)
class ExportBrandAssetResult:
    zip_path: str | None = None
    export_error: str | None = None


@dataclass(frozen=True)
class ExportLineageSnapshot:
    operation: str
    based_on_version_id: uuid.UUID | None = None
    source_input_id: uuid.UUID | None = None
    source_asset_id: uuid.UUID | None = None
    source_version_id: uuid.UUID | None = None
    source_version_number: int | None = None
    source_storage_key: str | None = None
    source_filename: str | None = None
    source_content_type: str | None = None
    source_size_bytes: int | None = None
    source_sha256: str | None = None
    snapshot_verified: bool = False
    validation_error: str | None = None


def get_campaign_or_404(campaign_id: uuid.UUID, db: Session) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )

    return campaign


def get_campaign_with_assets_or_404(
    campaign_id: uuid.UUID,
    db: Session,
) -> Campaign:
    statement = (
        select(Campaign)
        .options(
            selectinload(Campaign.assets)
            .selectinload(Asset.versions)
            .selectinload(AssetVersion.inputs),
            selectinload(Campaign.brand_asset_links).selectinload(
                CampaignBrandAsset.brand_asset
            ),
        )
        .where(Campaign.id == campaign_id)
    )
    campaign = db.scalar(statement)
    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )

    return campaign


def slugify_filename(value: str, fallback: str = "campaign") -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    return (slug or fallback)[:80]


def version_directory(asset: Asset, version: AssetVersion) -> str:
    asset_slug = slugify_filename(asset.title, fallback="asset")
    return f"{asset_slug}-{asset.id}/v{version.version_number}"


def unique_zip_path(path: str, used_paths: set[str]) -> str:
    if path not in used_paths:
        used_paths.add(path)
        return path

    directory, _, filename = path.rpartition("/")
    stem = PurePosixPath(filename).stem or "file"
    suffix = PurePosixPath(filename).suffix
    counter = 2

    while True:
        candidate_filename = f"{stem}-{counter}{suffix}"
        candidate = (
            f"{directory}/{candidate_filename}" if directory else candidate_filename
        )
        if candidate not in used_paths:
            used_paths.add(candidate)
            return candidate

        counter += 1


def safe_export_filename(filename: str | None, fallback: str) -> str:
    try:
        return normalize_artifact_filename(filename or fallback)
    except ValueError:
        return fallback


def parse_https_export_url(url: str) -> ParseResult:
    parsed_url = urlparse(url)
    if (
        parsed_url.scheme.lower() != "https"
        or not parsed_url.hostname
        or parsed_url.username is not None
        or parsed_url.password is not None
    ):
        raise ValueError("Export source URL must be an HTTPS URL without credentials")

    hostname = parsed_url.hostname.casefold()
    if (
        hostname == "localhost"
        or hostname.endswith(".localhost")
        or hostname.endswith(".local")
        or hostname.endswith(".internal")
    ):
        raise ValueError("Export source URL must use a public host")

    try:
        host_address = ip_address(hostname)
    except ValueError:
        host_address = None

    if host_address is not None and not host_address.is_global:
        raise ValueError("Export source URL must use a public host")

    return parsed_url


def durable_export_url(url: str | None) -> str | None:
    if not url:
        return None

    try:
        parsed_url = parse_https_export_url(url)
    except ValueError:
        return None

    return parsed_url._replace(query="", fragment="").geturl()


def sanitize_export_metadata(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: sanitize_export_metadata(item)
            for key, item in value.items()
            if not (
                isinstance(key, str)
                and key.casefold() in EPHEMERAL_EXPORT_URL_KEYS
            )
        }

    if isinstance(value, list):
        return [sanitize_export_metadata(item) for item in value]

    if isinstance(value, str):
        parsed_url = urlparse(value)
        if parsed_url.scheme.lower() in {"http", "https"} and parsed_url.hostname:
            return parsed_url._replace(query="", fragment="").geturl()

    return value


def campaign_brand_asset_links(
    campaign: Campaign,
) -> list[CampaignBrandAsset]:
    return sorted(
        campaign.brand_asset_links,
        key=lambda link: (
            link.brand_asset.asset_type.value,
            link.brand_asset.name.casefold(),
            link.role,
            str(link.id),
        ),
    )


def unique_campaign_brand_assets(campaign: Campaign) -> list[BrandAsset]:
    assets_by_id: dict[uuid.UUID, BrandAsset] = {}
    for link in campaign_brand_asset_links(campaign):
        assets_by_id.setdefault(link.brand_asset.id, link.brand_asset)

    return list(assets_by_id.values())


def brand_asset_zip_path(brand_asset: BrandAsset) -> str:
    asset_type = slugify_filename(
        brand_asset.asset_type.value,
        fallback="other",
    )
    asset_name = slugify_filename(
        brand_asset.name,
        fallback="brand-asset",
    )
    filename = safe_export_filename(
        brand_asset.filename,
        fallback=f"brand-asset-{brand_asset.id}",
    )
    return f"brand-assets/{asset_type}/{asset_name}-{brand_asset.id}/{filename}"


def download_verified_brand_asset_bytes(
    *,
    storage: B2StorageService,
    brand_asset: BrandAsset,
) -> bytes:
    body = storage.download_bytes(key=brand_asset.storage_key)

    if len(body) != brand_asset.size_bytes:
        raise ValueError("Brand asset size did not match stored metadata")

    if hashlib.sha256(body).hexdigest() != brand_asset.sha256:
        raise ValueError("Brand asset checksum did not match stored metadata")

    return body


def build_campaign_brand_asset_manifest(
    *,
    campaign: Campaign,
    export_results: dict[uuid.UUID, ExportBrandAssetResult],
) -> list[dict[str, object]]:
    links_by_asset_id: dict[uuid.UUID, list[CampaignBrandAsset]] = {}
    for link in campaign_brand_asset_links(campaign):
        links_by_asset_id.setdefault(link.brand_asset_id, []).append(link)

    return [
        {
            "id": str(brand_asset.id),
            "name": brand_asset.name,
            "asset_type": brand_asset.asset_type.value,
            "description": brand_asset.description,
            "usage_guidance": brand_asset.usage_guidance,
            "storage_key": brand_asset.storage_key,
            "filename": brand_asset.filename,
            "content_type": brand_asset.content_type,
            "size_bytes": brand_asset.size_bytes,
            "sha256": brand_asset.sha256,
            "tags": brand_asset.tags,
            "source_url": sanitize_export_metadata(brand_asset.source_url),
            "is_active": brand_asset.is_active,
            "created_at": brand_asset.created_at.isoformat(),
            "updated_at": brand_asset.updated_at.isoformat(),
            "attachments": [
                {
                    "id": str(link.id),
                    "role": link.role,
                    "attached_at": link.created_at.isoformat(),
                }
                for link in links_by_asset_id[brand_asset.id]
            ],
            "zip_path": export_results.get(
                brand_asset.id,
                ExportBrandAssetResult(),
            ).zip_path,
            "integrity_verified": bool(
                export_results.get(
                    brand_asset.id,
                    ExportBrandAssetResult(),
                ).zip_path
            ),
            "export_error": export_results.get(
                brand_asset.id,
                ExportBrandAssetResult(),
            ).export_error,
        }
        for brand_asset in unique_campaign_brand_assets(campaign)
    ]


def serialize_asset_version_input(
    version_input: AssetVersionInput,
) -> dict[str, object]:
    return {
        "id": str(version_input.id),
        "role": version_input.role,
        "storage_key": version_input.storage_key,
        "filename": version_input.filename,
        "content_type": version_input.content_type,
        "media_kind": version_input.media_kind,
        "size_bytes": version_input.size_bytes,
        "sha256": version_input.sha256,
        "source": version_input.source,
        "storage_ownership": version_input.storage_ownership,
        "source_asset_id": (
            str(version_input.source_asset_id)
            if version_input.source_asset_id is not None
            else None
        ),
        "source_version_id": (
            str(version_input.source_version_id)
            if version_input.source_version_id is not None
            else None
        ),
        "source_version_number": version_input.source_version_number,
        "brand_asset_id": (
            str(version_input.brand_asset_id)
            if version_input.brand_asset_id is not None
            else None
        ),
        "campaign_brand_asset_id": (
            str(version_input.campaign_brand_asset_id)
            if version_input.campaign_brand_asset_id is not None
            else None
        ),
        "brand_asset_type": version_input.brand_asset_type,
        "brand_asset_name": version_input.brand_asset_name,
        "usage_guidance": version_input.usage_guidance,
        "created_at": version_input.created_at.isoformat(),
    }


def export_input_record(
    reference: ExportInputReference,
    *,
    zip_path: str | None = None,
    export_error: str | None = None,
    exported_file: ExportedFileResult | None = None,
) -> dict[str, object]:
    return {
        "id": str(reference.id) if reference.id is not None else None,
        "role": reference.role,
        "storage_key": reference.storage_key,
        "url": durable_export_url(reference.url),
        "filename": reference.filename,
        "content_type": reference.content_type,
        "media_kind": reference.media_kind,
        "size_bytes": reference.size_bytes,
        "sha256": reference.sha256,
        "created_at": reference.created_at,
        "metadata_source": reference.source,
        "source": reference.input_source,
        "storage_ownership": reference.storage_ownership,
        "source_asset_id": reference.source_asset_id,
        "source_version_id": reference.source_version_id,
        "source_version_number": reference.source_version_number,
        "brand_asset_id": reference.brand_asset_id,
        "campaign_brand_asset_id": reference.campaign_brand_asset_id,
        "brand_asset_type": reference.brand_asset_type,
        "brand_asset_name": reference.brand_asset_name,
        "usage_guidance": reference.usage_guidance,
        "content_validation": reference.content_validation,
        "zip_path": zip_path,
        "exported_size_bytes": (
            exported_file.size_bytes if exported_file is not None else None
        ),
        "exported_sha256": (
            exported_file.sha256 if exported_file is not None else None
        ),
        "size_verified": (
            exported_file.size_verified if exported_file is not None else None
        ),
        "sha256_verified": (
            exported_file.sha256_verified if exported_file is not None else None
        ),
        "integrity_verified": (
            exported_file.integrity_verified
            if exported_file is not None
            else False
        ),
        "export_error": export_error,
    }


def build_campaign_export_manifest(
    *,
    campaign: Campaign,
    brand_assets: list[dict[str, object]],
    approved_assets: list[Asset],
    metadata_paths: dict[uuid.UUID, str],
    metadata_sources: dict[uuid.UUID, str],
    metadata_export_errors: dict[uuid.UUID, str],
    artifact_paths: dict[uuid.UUID, str],
    artifact_sources: dict[uuid.UUID, str],
    artifact_export_results: dict[uuid.UUID, ExportedFileResult],
    artifact_export_errors: dict[uuid.UUID, str],
    input_exports: dict[uuid.UUID, list[dict[str, object]]],
    lineage_snapshots: dict[uuid.UUID, ExportLineageSnapshot],
) -> dict[str, object]:
    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "campaign": {
            "id": str(campaign.id),
            "name": campaign.name,
            "product": campaign.product,
            "audience": campaign.audience,
            "status": campaign.status,
            "due_date": campaign.due_date.isoformat()
            if campaign.due_date is not None
            else None,
            "owner": campaign.owner,
            "goal": campaign.goal,
            "tone": campaign.tone,
            "brief": campaign.brief,
            "channels": campaign.channels,
            "brand_inputs": campaign.brand_inputs,
        },
        "brand_assets_manifest_path": "brand-assets/manifest.json",
        "brand_assets": brand_assets,
        "assets": [
            {
                "id": str(asset.id),
                "title": asset.title,
                "format": asset.format.value,
                "channel": asset.channel,
                "status": asset.status.value,
                "reviewer": asset.reviewer,
                "tags": asset.tags,
                "summary": asset.summary,
                "versions": [
                    {
                        "id": str(version.id),
                        "version_number": version.version_number,
                        "label": version.label,
                        "prompt": version.prompt,
                        "model": version.model,
                        "provider": version.provider,
                        "metadata_storage_key": version.storage_key,
                        "metadata_zip_path": metadata_paths[version.id],
                        "metadata_export_source": metadata_sources.get(version.id),
                        "metadata_export_error": metadata_export_errors.get(
                            version.id
                        ),
                        "artifact_storage_key": version.artifact_storage_key,
                        "artifact_filename": version.artifact_filename,
                        "artifact_content_type": version.artifact_content_type,
                        "artifact_size_bytes": version.artifact_size_bytes,
                        "artifact_zip_path": artifact_paths.get(version.id),
                        "artifact_export_source": artifact_sources.get(version.id),
                        "artifact_exported_size_bytes": (
                            artifact_export_results[version.id].size_bytes
                            if version.id in artifact_export_results
                            else None
                        ),
                        "artifact_exported_sha256": (
                            artifact_export_results[version.id].sha256
                            if version.id in artifact_export_results
                            else None
                        ),
                        "artifact_size_verified": (
                            artifact_export_results[version.id].size_verified
                            if version.id in artifact_export_results
                            else None
                        ),
                        "artifact_sha256_verified": (
                            artifact_export_results[version.id].sha256_verified
                            if version.id in artifact_export_results
                            else None
                        ),
                        "artifact_integrity_verified": (
                            artifact_export_results[
                                version.id
                            ].integrity_verified
                            if version.id in artifact_export_results
                            else False
                        ),
                        "artifact_export_error": artifact_export_errors.get(
                            version.id
                        ),
                        "input_assets": input_exports.get(version.id, []),
                        "export_lineage": build_export_lineage_record(
                            version=version,
                            lineage=lineage_snapshots.get(version.id),
                            metadata_paths=metadata_paths,
                            artifact_paths=artifact_paths,
                            artifact_export_results=artifact_export_results,
                            input_exports=input_exports,
                        ),
                        "generation_metadata": sanitize_export_metadata(
                            version.generation_metadata
                        ),
                    }
                    for version in sorted(
                        asset.versions,
                        key=lambda asset_version: asset_version.version_number,
                    )
                ],
            }
            for asset in approved_assets
        ],
    }


def build_version_metadata_sidecar(
    *,
    campaign: Campaign,
    asset: Asset,
    version: AssetVersion,
) -> dict[str, object]:
    return {
        "campaign": {
            "id": str(campaign.id),
            "name": campaign.name,
            "product": campaign.product,
            "audience": campaign.audience,
            "status": campaign.status,
            "due_date": campaign.due_date.isoformat()
            if campaign.due_date is not None
            else None,
            "owner": campaign.owner,
            "channels": campaign.channels,
            "brand_inputs": campaign.brand_inputs,
        },
        "asset": {
            "id": str(asset.id),
            "title": asset.title,
            "format": asset.format.value,
            "channel": asset.channel,
            "status": asset.status.value,
            "reviewer": asset.reviewer,
            "tags": asset.tags,
            "summary": asset.summary,
        },
        "version": {
            "id": str(version.id),
            "version_number": version.version_number,
            "label": version.label,
            "prompt": version.prompt,
            "model": version.model,
            "provider": version.provider,
            "storage_key": version.storage_key,
            "artifact_storage_key": version.artifact_storage_key,
            "artifact_filename": version.artifact_filename,
            "artifact_content_type": version.artifact_content_type,
            "artifact_size_bytes": version.artifact_size_bytes,
            "input_assets": [
                serialize_asset_version_input(version_input)
                for version_input in sorted(
                    version.inputs,
                    key=lambda item: item.created_at,
                )
            ],
            "generation_metadata": sanitize_export_metadata(
                version.generation_metadata
            ),
        },
        "exported_at": datetime.now(UTC).isoformat(),
    }


def encode_pretty_json(data: object) -> bytes:
    return json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")


def optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value

    return None


def optional_int(value: object) -> int | None:
    if isinstance(value, int):
        return value

    if isinstance(value, str) and value.isdigit():
        return int(value)

    return None


def optional_metadata_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value

    return {}


def optional_asset_metadata_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []

    return [item for item in value if isinstance(item, dict)]


def normalized_content_type(value: str | None) -> str | None:
    content_type = optional_string(value)
    if content_type is None:
        return None

    return content_type.split(";", maxsplit=1)[0].strip().casefold()


def version_artifact_sha256(version: AssetVersion) -> str | None:
    metadata = version.generation_metadata or {}
    artifact_flow = optional_metadata_dict(metadata.get("artifact_flow"))
    artifact_flow_key = optional_string(artifact_flow.get("storage_key"))
    if artifact_flow_key in {None, version.artifact_storage_key}:
        for field_name in ("source_sha256", "sha256"):
            checksum = optional_string(artifact_flow.get(field_name))
            if checksum and re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
                return checksum.casefold()

    for asset_record in optional_asset_metadata_list(metadata.get("assets")):
        storage_key = optional_string(asset_record.get("storage_key"))
        if storage_key != version.artifact_storage_key:
            continue

        checksum = optional_string(asset_record.get("sha256"))
        if checksum and re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
            return checksum.casefold()

    return None


def build_video_refinement_export_lineage(
    *,
    asset: Asset,
    version: AssetVersion,
) -> ExportLineageSnapshot | None:
    if asset.format != AssetFormat.video_concept:
        return None

    metadata = version.generation_metadata or {}
    provenance = optional_metadata_dict(metadata.get("provenance"))
    request = optional_metadata_dict(metadata.get("request"))
    provenance_request = optional_metadata_dict(provenance.get("request"))
    operation = VideoGenerationOperation.refinement.value
    operation_values = tuple(
        value
        for value in (
            optional_string(metadata.get("operation")),
            optional_string(provenance.get("operation")),
            optional_string(request.get("operation")),
            optional_string(provenance_request.get("operation")),
        )
        if value is not None
    )
    if operation not in operation_values:
        return None

    source_inputs = [
        version_input
        for version_input in version.inputs
        if version_input.role == VIDEO_REFINEMENT_CONTRACT.source_role
        and (
            version_input.source == "source_version_artifact"
            or version_input.source_version_id is not None
        )
    ]
    source_input = source_inputs[0] if len(source_inputs) == 1 else None
    snapshot = ExportLineageSnapshot(
        operation=operation,
        source_input_id=source_input.id if source_input is not None else None,
        source_asset_id=(
            source_input.source_asset_id if source_input is not None else None
        ),
        source_version_id=(
            source_input.source_version_id if source_input is not None else None
        ),
        source_version_number=(
            source_input.source_version_number if source_input is not None else None
        ),
        source_storage_key=(
            source_input.storage_key if source_input is not None else None
        ),
        source_filename=(
            source_input.filename if source_input is not None else None
        ),
        source_content_type=(
            source_input.content_type if source_input is not None else None
        ),
        source_size_bytes=(
            source_input.size_bytes if source_input is not None else None
        ),
        source_sha256=(
            source_input.sha256.casefold()
            if source_input is not None and source_input.sha256 is not None
            else None
        ),
    )

    def invalid(detail: str) -> ExportLineageSnapshot:
        return replace(snapshot, validation_error=detail)

    if any(value != operation for value in operation_values):
        return invalid("Video refinement operation metadata is inconsistent")
    if len(source_inputs) != 1:
        return invalid("Video refinement must have one canonical source snapshot")

    based_on_version = optional_string(metadata.get("based_on_version_id"))
    if based_on_version is None:
        return invalid("Video refinement is missing its parent version identifier")
    try:
        based_on_version_id = uuid.UUID(based_on_version)
    except ValueError:
        return invalid("Video refinement parent version identifier is invalid")
    snapshot = replace(snapshot, based_on_version_id=based_on_version_id)

    lineage_identifiers = [
        optional_string(provenance.get("based_on_version_id")),
        optional_string(request.get("based_on_version_id")),
        optional_string(provenance_request.get("based_on_version_id")),
    ]
    for container in (
        optional_metadata_dict(metadata.get("source_resolution")),
        optional_metadata_dict(provenance.get("source_resolution")),
        optional_metadata_dict(request.get("source_resolution")),
        optional_metadata_dict(provenance_request.get("source_resolution")),
    ):
        lineage_identifiers.append(
            optional_string(container.get("source_version_id"))
        )
    if any(
        identifier != based_on_version
        for identifier in lineage_identifiers
        if identifier is not None
    ):
        return invalid("Video refinement parent version metadata is inconsistent")

    parent_version = next(
        (
            asset_version
            for asset_version in asset.versions
            if asset_version.id == based_on_version_id
        ),
        None,
    )
    if parent_version is None:
        return invalid("Video refinement parent version is not part of this asset")
    if (
        version.version_number < 2
        or parent_version.version_number != version.version_number - 1
    ):
        return invalid("Video refinement parent must be the previous version")
    if source_input is None:
        return invalid("Video refinement source snapshot is missing")
    if source_input.source != "source_version_artifact":
        return invalid("Video refinement source snapshot has an invalid origin")
    if source_input.storage_ownership != "source_asset_version":
        return invalid("Video refinement source snapshot has invalid ownership")
    if source_input.source_asset_id != asset.id:
        return invalid("Video refinement source belongs to a different asset")
    if source_input.source_version_id != based_on_version_id:
        return invalid("Video refinement source version does not match its parent")
    if source_input.source_version_number != parent_version.version_number:
        return invalid("Video refinement source version number is inconsistent")
    if source_input.storage_key != parent_version.artifact_storage_key:
        return invalid("Video refinement source storage key does not match its parent")
    if source_input.filename != parent_version.artifact_filename:
        return invalid("Video refinement source filename does not match its parent")
    if (
        normalized_content_type(source_input.content_type)
        != normalized_content_type(parent_version.artifact_content_type)
    ):
        return invalid("Video refinement source content type is inconsistent")
    if source_input.size_bytes != parent_version.artifact_size_bytes:
        return invalid("Video refinement source size does not match its parent")

    parent_sha256 = version_artifact_sha256(parent_version)
    if parent_sha256 is None or snapshot.source_sha256 != parent_sha256:
        return invalid("Video refinement source checksum does not match its parent")

    return replace(snapshot, snapshot_verified=True)


def refinement_source_export_error(
    *,
    lineage: ExportLineageSnapshot | None,
    reference: ExportInputReference,
) -> str | None:
    if lineage is None or reference.role != VIDEO_REFINEMENT_CONTRACT.source_role:
        return None
    if (
        reference.brand_asset_id is not None
        and reference.input_source == "campaign_brand_asset"
    ):
        return None
    if not lineage.snapshot_verified:
        return "Video refinement source was not exported because lineage is invalid"

    reference_sha256 = (
        reference.sha256.casefold() if reference.sha256 is not None else None
    )
    if (
        reference.storage_key != lineage.source_storage_key
        or reference.input_source != "source_version_artifact"
        or reference.storage_ownership != "source_asset_version"
        or reference.source_asset_id != str(lineage.source_asset_id)
        or reference.source_version_id != str(lineage.source_version_id)
        or reference.source_version_number != lineage.source_version_number
        or reference.filename != lineage.source_filename
        or normalized_content_type(reference.content_type)
        != normalized_content_type(lineage.source_content_type)
        or reference.size_bytes != lineage.source_size_bytes
        or reference_sha256 != lineage.source_sha256
    ):
        return "Video refinement source was not exported because lineage changed"

    return None


def build_export_lineage_record(
    *,
    version: AssetVersion,
    lineage: ExportLineageSnapshot | None,
    metadata_paths: dict[uuid.UUID, str],
    artifact_paths: dict[uuid.UUID, str],
    artifact_export_results: dict[uuid.UUID, ExportedFileResult],
    input_exports: dict[uuid.UUID, list[dict[str, object]]],
) -> dict[str, object] | None:
    if lineage is None:
        return None

    parent_version_id = lineage.based_on_version_id
    parent_artifact_result = (
        artifact_export_results.get(parent_version_id)
        if parent_version_id is not None
        else None
    )
    source_export = None
    if (
        lineage.source_storage_key is not None
        and lineage.source_version_id is not None
    ):
        source_export = next(
            (
                input_record
                for input_record in input_exports.get(version.id, [])
                if input_record.get("storage_key")
                == lineage.source_storage_key
                and input_record.get("source_version_id")
                == str(lineage.source_version_id)
            ),
            None,
        )
    parent_integrity_verified = bool(
        parent_artifact_result is not None
        and parent_artifact_result.integrity_verified
    )
    source_integrity_verified = bool(
        source_export is not None
        and source_export.get("integrity_verified") is True
    )
    errors: list[str] = []
    if lineage.validation_error is not None:
        errors.append(lineage.validation_error)
    if lineage.snapshot_verified and not parent_integrity_verified:
        errors.append("Parent artifact was not integrity-verified during export")
    if lineage.snapshot_verified and not source_integrity_verified:
        source_error = (
            optional_string(source_export.get("export_error"))
            if source_export is not None
            else None
        )
        errors.append(
            source_error
            or "Refinement source was not integrity-verified during export"
        )

    return {
        "operation": lineage.operation,
        "based_on_version_id": (
            str(parent_version_id) if parent_version_id is not None else None
        ),
        "source_input_id": (
            str(lineage.source_input_id)
            if lineage.source_input_id is not None
            else None
        ),
        "source_asset_id": (
            str(lineage.source_asset_id)
            if lineage.source_asset_id is not None
            else None
        ),
        "source_version_id": (
            str(lineage.source_version_id)
            if lineage.source_version_id is not None
            else None
        ),
        "source_version_number": lineage.source_version_number,
        "source_storage_key": lineage.source_storage_key,
        "source_sha256": lineage.source_sha256,
        "parent_metadata_zip_path": (
            metadata_paths.get(parent_version_id)
            if parent_version_id is not None
            else None
        ),
        "parent_artifact_zip_path": (
            artifact_paths.get(parent_version_id)
            if parent_version_id is not None
            else None
        ),
        "source_input_zip_path": (
            source_export.get("zip_path") if source_export is not None else None
        ),
        "snapshot_verified": lineage.snapshot_verified,
        "parent_artifact_integrity_verified": parent_integrity_verified,
        "source_input_integrity_verified": source_integrity_verified,
        "integrity_verified": (
            lineage.snapshot_verified
            and parent_integrity_verified
            and source_integrity_verified
        ),
        "export_error": "; ".join(errors) if errors else None,
    }


def asset_version_input_reference(
    version_input: AssetVersionInput,
    *,
    content_validation: dict[str, object] | None = None,
) -> ExportInputReference:
    return ExportInputReference(
        id=version_input.id,
        role=version_input.role,
        storage_key=version_input.storage_key,
        url=None,
        filename=version_input.filename,
        content_type=version_input.content_type,
        media_kind=version_input.media_kind,
        size_bytes=version_input.size_bytes,
        sha256=version_input.sha256,
        created_at=version_input.created_at.isoformat(),
        source="asset_version_input",
        input_source=version_input.source,
        storage_ownership=version_input.storage_ownership,
        source_asset_id=(
            str(version_input.source_asset_id)
            if version_input.source_asset_id is not None
            else None
        ),
        source_version_id=(
            str(version_input.source_version_id)
            if version_input.source_version_id is not None
            else None
        ),
        source_version_number=version_input.source_version_number,
        brand_asset_id=(
            str(version_input.brand_asset_id)
            if version_input.brand_asset_id is not None
            else None
        ),
        campaign_brand_asset_id=(
            str(version_input.campaign_brand_asset_id)
            if version_input.campaign_brand_asset_id is not None
            else None
        ),
        brand_asset_type=version_input.brand_asset_type,
        brand_asset_name=version_input.brand_asset_name,
        usage_guidance=version_input.usage_guidance,
        content_validation=content_validation,
    )


def metadata_input_asset_references(
    version: AssetVersion,
) -> list[ExportInputReference]:
    metadata = version.generation_metadata or {}
    provenance = optional_metadata_dict(metadata.get("provenance"))
    request = optional_metadata_dict(metadata.get("request"))
    provenance_request = optional_metadata_dict(provenance.get("request"))
    references: list[ExportInputReference] = []

    for source_name, input_items in (
        (
            "generation_metadata_input_asset",
            optional_asset_metadata_list(metadata.get("input_assets")),
        ),
        (
            "generation_provenance_input_asset",
            optional_asset_metadata_list(provenance.get("input_assets")),
        ),
        (
            "generation_metadata_source_input_asset",
            optional_asset_metadata_list(metadata.get("source_input_assets")),
        ),
        (
            "generation_metadata_context_asset",
            optional_asset_metadata_list(metadata.get("context_assets")),
        ),
        (
            "generation_request_source_input_asset",
            optional_asset_metadata_list(request.get("source_input_assets")),
        ),
        (
            "generation_request_context_asset",
            optional_asset_metadata_list(request.get("context_assets")),
        ),
        (
            "generation_provenance_source_input_asset",
            optional_asset_metadata_list(provenance.get("source_input_assets")),
        ),
        (
            "generation_provenance_context_asset",
            optional_asset_metadata_list(provenance.get("context_assets")),
        ),
        (
            "generation_provenance_request_source_input_asset",
            optional_asset_metadata_list(
                provenance_request.get("source_input_assets")
            ),
        ),
        (
            "generation_provenance_request_context_asset",
            optional_asset_metadata_list(provenance_request.get("context_assets")),
        ),
    ):
        for input_item in input_items:
            storage_key = optional_string(input_item.get("storage_key"))
            url = optional_string(input_item.get("url"))
            if not storage_key and not url:
                continue

            references.append(
                ExportInputReference(
                    id=None,
                    role=optional_string(input_item.get("role")),
                    storage_key=storage_key,
                    url=url,
                    filename=optional_string(input_item.get("filename"))
                    or filename_from_url(url),
                    content_type=optional_string(input_item.get("content_type")),
                    media_kind=optional_string(input_item.get("media_kind")),
                    size_bytes=optional_int(input_item.get("size_bytes")),
                    sha256=optional_string(input_item.get("sha256")),
                    created_at=optional_string(input_item.get("created_at")),
                    source=source_name,
                    input_source=optional_string(input_item.get("source")),
                    storage_ownership=optional_string(
                        input_item.get("storage_ownership")
                    ),
                    source_asset_id=optional_string(
                        input_item.get("source_asset_id")
                    ),
                    source_version_id=optional_string(
                        input_item.get("source_version_id")
                    ),
                    source_version_number=optional_int(
                        input_item.get("source_version_number")
                    ),
                    brand_asset_id=optional_string(
                        input_item.get("brand_asset_id")
                    ),
                    campaign_brand_asset_id=optional_string(
                        input_item.get("campaign_brand_asset_id")
                    ),
                    brand_asset_type=optional_string(
                        input_item.get("brand_asset_type")
                    ),
                    brand_asset_name=optional_string(
                        input_item.get("brand_asset_name")
                    ),
                    usage_guidance=optional_string(
                        input_item.get("usage_guidance")
                    ),
                    content_validation=(
                        optional_metadata_dict(
                            input_item.get("content_validation")
                        )
                        or None
                    ),
                )
            )

    return references


def input_reference_identity(
    reference: ExportInputReference,
) -> tuple[str, str, str]:
    if reference.storage_key:
        return ("storage_key", reference.storage_key, reference.role or "")

    if reference.url:
        return ("url", durable_export_url(reference.url) or "", reference.role or "")

    if reference.id is not None:
        return ("id", str(reference.id), reference.role or "")

    return ("filename", reference.filename or "", reference.role or "")


def version_input_references(version: AssetVersion) -> list[ExportInputReference]:
    references: list[ExportInputReference] = []
    seen: set[tuple[str, str, str]] = set()
    metadata_references = metadata_input_asset_references(version)
    metadata_by_identity = {
        input_reference_identity(reference): reference
        for reference in metadata_references
    }

    for version_input in sorted(version.inputs, key=lambda item: item.created_at):
        raw_reference = asset_version_input_reference(version_input)
        metadata_reference = metadata_by_identity.get(
            input_reference_identity(raw_reference)
        )
        reference = (
            replace(
                raw_reference,
                content_validation=metadata_reference.content_validation,
            )
            if metadata_reference is not None
            else raw_reference
        )
        seen.add(input_reference_identity(reference))
        if reference.storage_key or reference.url:
            references.append(reference)

    for reference in metadata_references:
        identity = input_reference_identity(reference)
        if identity in seen:
            continue

        seen.add(identity)
        references.append(reference)

    return references


def filename_from_url(url: str | None) -> str | None:
    if not url:
        return None

    filename = unquote(urlparse(url).path.rsplit("/", maxsplit=1)[-1]).strip()
    return filename or None


def generated_artifact_references(
    version: AssetVersion,
) -> list[ExportArtifactReference]:
    metadata = version.generation_metadata or {}
    references: list[ExportArtifactReference] = []
    artifact_flow = optional_metadata_dict(metadata.get("artifact_flow"))

    if artifact_flow:
        references.append(
            ExportArtifactReference(
                storage_key=optional_string(artifact_flow.get("storage_key")),
                url=None,
                filename=optional_string(artifact_flow.get("filename")),
                content_type=optional_string(artifact_flow.get("content_type")),
                size_bytes=optional_int(artifact_flow.get("size_bytes")),
                sha256=optional_string(artifact_flow.get("sha256")),
                source="generation_metadata_artifact_flow",
            )
        )
        references.append(
            ExportArtifactReference(
                storage_key=optional_string(artifact_flow.get("source_storage_key")),
                url=None,
                filename=optional_string(artifact_flow.get("filename")),
                content_type=optional_string(artifact_flow.get("content_type")),
                size_bytes=None,
                sha256=optional_string(artifact_flow.get("source_sha256")),
                source="genblaze_source_artifact",
            )
        )

    provenance = optional_metadata_dict(metadata.get("provenance"))
    for source_name, asset_items in (
        (
            "generation_metadata_asset",
            optional_asset_metadata_list(metadata.get("assets")),
        ),
        (
            "generation_provenance_asset",
            optional_asset_metadata_list(provenance.get("assets")),
        ),
    ):
        for asset_item in asset_items:
            url = optional_string(asset_item.get("url"))
            storage_key = optional_string(asset_item.get("storage_key"))
            if not url and not storage_key:
                continue

            references.append(
                ExportArtifactReference(
                    storage_key=storage_key,
                    url=url,
                    filename=optional_string(asset_item.get("filename"))
                    or filename_from_url(url),
                    content_type=optional_string(asset_item.get("content_type")),
                    size_bytes=optional_int(asset_item.get("size_bytes")),
                    sha256=optional_string(asset_item.get("sha256")),
                    source=source_name,
                )
            )

    return [
        reference
        for reference in references
        if reference.storage_key or reference.url
    ]


def version_artifact_reference(version: AssetVersion) -> ExportArtifactReference | None:
    if version.artifact_storage_key:
        artifact_flow = optional_metadata_dict(
            (version.generation_metadata or {}).get("artifact_flow")
        )
        artifact_flow_key = optional_string(artifact_flow.get("storage_key"))
        return ExportArtifactReference(
            storage_key=version.artifact_storage_key,
            url=None,
            filename=version.artifact_filename,
            content_type=version.artifact_content_type,
            size_bytes=version.artifact_size_bytes,
            sha256=(
                optional_string(artifact_flow.get("sha256"))
                if artifact_flow_key in {None, version.artifact_storage_key}
                else None
            ),
            source="asset_version_artifact",
        )

    for reference in generated_artifact_references(version):
        return reference

    return None


def safe_artifact_filename(
    *,
    version: AssetVersion,
    reference: ExportArtifactReference,
) -> str:
    try:
        return normalize_artifact_filename(
            reference.filename
            or version.artifact_filename
            or f"artifact-{version.id}"
        )
    except ValueError:
        return f"artifact-{version.id}"


def iter_download_url_chunks(
    *,
    url: str,
    max_size_bytes: int,
) -> Iterator[bytes]:
    parse_https_export_url(url)

    request = Request(
        url,
        headers={"User-Agent": "SereneSet-Spark/1.0"},
    )

    with urlopen(request, timeout=60) as response:
        parse_https_export_url(response.geturl())
        raw_content_length = response.headers.get("Content-Length")
        content_length = (
            int(raw_content_length)
            if raw_content_length is not None and raw_content_length.isdigit()
            else None
        )
        if content_length is not None and content_length > max_size_bytes:
            raise StorageObjectTooLargeError(
                "Generated artifact exceeds the configured export size limit"
            )

        downloaded_bytes = 0
        while True:
            chunk = response.read(EXPORT_STREAM_CHUNK_SIZE_BYTES)
            if not chunk:
                break

            downloaded_bytes += len(chunk)
            if downloaded_bytes > max_size_bytes:
                raise StorageObjectTooLargeError(
                    "Generated artifact exceeds the configured export size limit"
                )

            yield chunk

    if downloaded_bytes == 0:
        raise ValueError("Generated artifact URL returned an empty body")

    if content_length is not None and downloaded_bytes != content_length:
        raise ValueError(
            "Generated artifact response size did not match its content length"
        )


def is_video_export_artifact(
    *,
    version: AssetVersion,
    reference: ExportArtifactReference,
) -> bool:
    content_type = (
        reference.content_type
        or version.artifact_content_type
        or ""
    ).split(";", maxsplit=1)[0].strip().lower()
    if content_type.startswith("video/"):
        return True

    filename = (
        reference.filename
        or version.artifact_filename
        or reference.storage_key
        or filename_from_url(reference.url)
        or ""
    )
    return PurePosixPath(filename.lower()).suffix in VIDEO_ARTIFACT_SUFFIXES


def iter_export_source_chunks(
    *,
    storage: B2StorageService,
    storage_key: str | None,
    url: str | None,
    max_size_bytes: int,
) -> Iterator[bytes]:
    if storage_key:
        yield from storage.iter_download_chunks(
            key=storage_key,
            chunk_size_bytes=EXPORT_STREAM_CHUNK_SIZE_BYTES,
            max_size_bytes=max_size_bytes,
        )
        return

    if url:
        yield from iter_download_url_chunks(
            url=url,
            max_size_bytes=max_size_bytes,
        )
        return

    raise ValueError("Export reference did not include a B2 key or URL")


def write_chunks_to_zip(
    *,
    export_zip: ZipFile,
    zip_path: str,
    chunks: Iterator[bytes],
    compression: int,
    expected_size_bytes: int | None,
    expected_sha256: str | None,
) -> ExportedFileResult:
    if expected_size_bytes is not None and expected_size_bytes < 1:
        raise ValueError("Expected export size must be greater than zero")

    expected_checksum = (
        expected_sha256.strip().lower() if expected_sha256 is not None else None
    )
    if expected_checksum is not None and not re.fullmatch(
        r"[0-9a-f]{64}",
        expected_checksum,
    ):
        raise ValueError("Expected export SHA-256 is invalid")

    with SpooledTemporaryFile(
        max_size=EXPORT_SPOOL_MEMORY_LIMIT_BYTES,
        mode="w+b",
    ) as staged_file:
        exported_size_bytes = 0
        exported_checksum = hashlib.sha256()
        for chunk in chunks:
            if not isinstance(chunk, bytes):
                raise ValueError("Export source returned non-binary data")

            staged_file.write(chunk)
            exported_checksum.update(chunk)
            exported_size_bytes += len(chunk)

        if exported_size_bytes == 0:
            raise ValueError("Export source was empty")

        size_verified = (
            exported_size_bytes == expected_size_bytes
            if expected_size_bytes is not None
            else None
        )
        if size_verified is False:
            raise ValueError("Export source size did not match stored metadata")

        exported_sha256 = exported_checksum.hexdigest()
        sha256_verified = (
            exported_sha256 == expected_checksum
            if expected_checksum is not None
            else None
        )
        if sha256_verified is False:
            raise ValueError("Export source checksum did not match stored metadata")

        staged_file.seek(0)
        zip_info = ZipInfo(
            filename=zip_path,
            date_time=datetime.now(UTC).timetuple()[:6],
        )
        zip_info.compress_type = compression
        zip_info.external_attr = 0o600 << 16
        zip_info.file_size = exported_size_bytes

        with export_zip.open(zip_info, mode="w") as zip_entry:
            copyfileobj(
                staged_file,
                zip_entry,
                length=EXPORT_STREAM_CHUNK_SIZE_BYTES,
            )

    return ExportedFileResult(
        size_bytes=exported_size_bytes,
        sha256=exported_sha256,
        size_verified=size_verified,
        sha256_verified=sha256_verified,
    )


def write_artifact_to_zip(
    *,
    export_zip: ZipFile,
    zip_path: str,
    storage: B2StorageService,
    version: AssetVersion,
    reference: ExportArtifactReference,
    max_size_bytes: int,
) -> ExportedFileResult:
    if (
        reference.size_bytes is not None
        and reference.size_bytes > max_size_bytes
    ):
        raise StorageObjectTooLargeError(
            "Generated artifact exceeds the configured export size limit"
        )

    return write_chunks_to_zip(
        export_zip=export_zip,
        zip_path=zip_path,
        chunks=iter_export_source_chunks(
            storage=storage,
            storage_key=reference.storage_key,
            url=reference.url,
            max_size_bytes=max_size_bytes,
        ),
        compression=(
            ZIP_STORED
            if is_video_export_artifact(
                version=version,
                reference=reference,
            )
            else ZIP_DEFLATED
        ),
        expected_size_bytes=reference.size_bytes,
        expected_sha256=reference.sha256,
    )


def is_video_export_input(reference: ExportInputReference) -> bool:
    if (reference.media_kind or "").strip().casefold() == "video":
        return True

    content_type = (reference.content_type or "").split(";", maxsplit=1)[0]
    if content_type.strip().casefold().startswith("video/"):
        return True

    filename = reference.filename or reference.storage_key or filename_from_url(
        reference.url
    )
    return PurePosixPath((filename or "").casefold()).suffix in VIDEO_ARTIFACT_SUFFIXES


def write_input_to_zip(
    *,
    export_zip: ZipFile,
    zip_path: str,
    storage: B2StorageService,
    reference: ExportInputReference,
    max_non_video_size_bytes: int,
    max_video_size_bytes: int,
) -> ExportedFileResult:
    is_video = is_video_export_input(reference)
    max_size_bytes = (
        max_video_size_bytes if is_video else max_non_video_size_bytes
    )
    if reference.size_bytes is not None and reference.size_bytes > max_size_bytes:
        raise StorageObjectTooLargeError(
            "Input asset exceeds the configured export size limit"
        )

    return write_chunks_to_zip(
        export_zip=export_zip,
        zip_path=zip_path,
        chunks=iter_export_source_chunks(
            storage=storage,
            storage_key=reference.storage_key,
            url=reference.url,
            max_size_bytes=max_size_bytes,
        ),
        compression=ZIP_STORED if is_video else ZIP_DEFLATED,
        expected_size_bytes=reference.size_bytes,
        expected_sha256=reference.sha256,
    )


def make_input_zip_path(
    *,
    asset: Asset,
    version: AssetVersion,
    reference: ExportInputReference,
    used_paths: set[str],
) -> str:
    role_path = slugify_filename(reference.role or "reference", fallback="reference")
    filename = safe_export_filename(
        reference.filename,
        fallback=f"input-{reference.id or version.id}",
    )

    return unique_zip_path(
        f"inputs/{version_directory(asset, version)}/{role_path}/{filename}",
        used_paths,
    )


def download_sanitized_metadata_sidecar(
    *,
    storage: B2StorageService,
    storage_key: str,
) -> bytes:
    body = b"".join(
        storage.iter_download_chunks(
            key=storage_key,
            chunk_size_bytes=EXPORT_STREAM_CHUNK_SIZE_BYTES,
            max_size_bytes=MAX_EXPORT_METADATA_SIZE_BYTES,
        )
    )
    parsed_sidecar = json.loads(body.decode("utf-8"))
    if not isinstance(parsed_sidecar, dict):
        raise ValueError("Stored metadata sidecar must contain a JSON object")

    return encode_pretty_json(sanitize_export_metadata(parsed_sidecar))


def write_campaign_export_zip(
    *,
    campaign: Campaign,
    storage: B2StorageService,
    destination: BinaryIO | str,
    max_video_artifact_size_bytes: int,
    max_non_video_input_size_bytes: int,
    max_video_input_size_bytes: int,
) -> None:
    if max_video_artifact_size_bytes < 1:
        raise ValueError("Maximum exported video size must be greater than zero")
    if max_non_video_input_size_bytes < 1:
        raise ValueError(
            "Maximum exported non-video input size must be greater than zero"
        )
    if max_video_input_size_bytes < 1:
        raise ValueError("Maximum exported video input size must be greater than zero")

    approved_assets = sorted(
        (
            asset
            for asset in campaign.assets
            if asset.status == ReviewStatus.approved
        ),
        key=lambda asset: (asset.channel, asset.title),
    )
    lineage_snapshots: dict[uuid.UUID, ExportLineageSnapshot] = {}
    for asset in approved_assets:
        for version in asset.versions:
            lineage = build_video_refinement_export_lineage(
                asset=asset,
                version=version,
            )
            if lineage is not None:
                lineage_snapshots[version.id] = lineage

    metadata_paths: dict[uuid.UUID, str] = {}
    metadata_sources: dict[uuid.UUID, str] = {}
    metadata_export_errors: dict[uuid.UUID, str] = {}
    artifact_paths: dict[uuid.UUID, str] = {}
    artifact_sources: dict[uuid.UUID, str] = {}
    artifact_export_results: dict[uuid.UUID, ExportedFileResult] = {}
    artifact_export_errors: dict[uuid.UUID, str] = {}
    input_exports: dict[uuid.UUID, list[dict[str, object]]] = {}
    used_input_paths: set[str] = set()
    brand_asset_export_results: dict[uuid.UUID, ExportBrandAssetResult] = {}

    with ZipFile(destination, mode="w", compression=ZIP_DEFLATED) as export_zip:
        for brand_asset in unique_campaign_brand_assets(campaign):
            zip_path = brand_asset_zip_path(brand_asset)
            try:
                export_zip.writestr(
                    zip_path,
                    download_verified_brand_asset_bytes(
                        storage=storage,
                        brand_asset=brand_asset,
                    ),
                )
                brand_asset_export_results[brand_asset.id] = (
                    ExportBrandAssetResult(zip_path=zip_path)
                )
            except (
                StorageConfigurationError,
                BotoCoreError,
                ClientError,
                OSError,
                ValueError,
            ):
                brand_asset_export_results[brand_asset.id] = (
                    ExportBrandAssetResult(
                        export_error=(
                            "Brand asset could not be downloaded or did not "
                            "match its stored integrity metadata"
                        )
                    )
                )

        brand_assets = build_campaign_brand_asset_manifest(
            campaign=campaign,
            export_results=brand_asset_export_results,
        )
        export_zip.writestr(
            "brand-assets/manifest.json",
            encode_pretty_json(
                {
                    "campaign_id": str(campaign.id),
                    "exported_at": datetime.now(UTC).isoformat(),
                    "brand_assets": brand_assets,
                }
            ),
        )

        for asset in approved_assets:
            for version in sorted(
                asset.versions,
                key=lambda asset_version: asset_version.version_number,
            ):
                base_path = version_directory(asset, version)
                metadata_path = f"metadata/{base_path}/metadata.json"
                metadata_paths[version.id] = metadata_path
                generated_metadata = build_version_metadata_sidecar(
                    campaign=campaign,
                    asset=asset,
                    version=version,
                )
                try:
                    export_zip.writestr(
                        metadata_path,
                        download_sanitized_metadata_sidecar(
                            storage=storage,
                            storage_key=version.storage_key,
                        ),
                    )
                    metadata_sources[version.id] = "b2_sidecar"
                except (
                    StorageConfigurationError,
                    BotoCoreError,
                    ClientError,
                    OSError,
                    StorageObjectTooLargeError,
                    StorageOperationError,
                    UnicodeDecodeError,
                    ValueError,
                ):
                    export_zip.writestr(
                        metadata_path,
                        encode_pretty_json(generated_metadata),
                    )
                    metadata_sources[version.id] = "generated_fallback"
                    metadata_export_errors[version.id] = (
                        "B2 sidecar metadata could not be downloaded during "
                        "export; wrote generated fallback metadata"
                    )

                artifact_reference = version_artifact_reference(version)
                if artifact_reference is not None:
                    artifact_filename = safe_artifact_filename(
                        version=version,
                        reference=artifact_reference,
                    )
                    artifact_path = f"artifacts/{base_path}/{artifact_filename}"
                    try:
                        max_artifact_size_bytes = (
                            max_video_artifact_size_bytes
                            if is_video_export_artifact(
                                version=version,
                                reference=artifact_reference,
                            )
                            else MAX_EXPORT_ARTIFACT_SIZE_BYTES
                        )
                        artifact_export_results[version.id] = write_artifact_to_zip(
                            export_zip=export_zip,
                            zip_path=artifact_path,
                            storage=storage,
                            version=version,
                            reference=artifact_reference,
                            max_size_bytes=max_artifact_size_bytes,
                        )
                        artifact_paths[version.id] = artifact_path
                        artifact_sources[version.id] = artifact_reference.source
                    except (
                        StorageConfigurationError,
                        BotoCoreError,
                        ClientError,
                        OSError,
                        StorageObjectTooLargeError,
                        StorageOperationError,
                        URLError,
                        ValueError,
                    ):
                        artifact_export_errors[version.id] = (
                            "Artifact could not be downloaded from its source "
                            "during export"
                        )

                for input_reference in version_input_references(version):
                    lineage_error = refinement_source_export_error(
                        lineage=lineage_snapshots.get(version.id),
                        reference=input_reference,
                    )
                    if lineage_error is not None:
                        input_exports.setdefault(version.id, []).append(
                            export_input_record(
                                input_reference,
                                export_error=lineage_error,
                            )
                        )
                        continue

                    input_path = make_input_zip_path(
                        asset=asset,
                        version=version,
                        reference=input_reference,
                        used_paths=used_input_paths,
                    )
                    try:
                        exported_file = write_input_to_zip(
                            export_zip=export_zip,
                            zip_path=input_path,
                            storage=storage,
                            reference=input_reference,
                            max_non_video_size_bytes=(
                                max_non_video_input_size_bytes
                            ),
                            max_video_size_bytes=max_video_input_size_bytes,
                        )
                        input_exports.setdefault(version.id, []).append(
                            export_input_record(
                                input_reference,
                                zip_path=input_path,
                                exported_file=exported_file,
                            )
                        )
                    except (
                        StorageConfigurationError,
                        BotoCoreError,
                        ClientError,
                        OSError,
                        StorageObjectTooLargeError,
                        StorageOperationError,
                        URLError,
                        ValueError,
                    ):
                        input_exports.setdefault(version.id, []).append(
                            export_input_record(
                                input_reference,
                                export_error=(
                                    "Input asset could not be downloaded from "
                                    "its source during export"
                                ),
                            )
                        )

        manifest = build_campaign_export_manifest(
            campaign=campaign,
            brand_assets=brand_assets,
            approved_assets=approved_assets,
            metadata_paths=metadata_paths,
            metadata_sources=metadata_sources,
            metadata_export_errors=metadata_export_errors,
            artifact_paths=artifact_paths,
            artifact_sources=artifact_sources,
            artifact_export_results=artifact_export_results,
            artifact_export_errors=artifact_export_errors,
            input_exports=input_exports,
            lineage_snapshots=lineage_snapshots,
        )
        export_zip.writestr(
            "manifest.json",
            json.dumps(manifest, indent=2, ensure_ascii=False),
        )


def make_campaign_export_zip(
    *,
    campaign: Campaign,
    storage: B2StorageService,
    max_video_artifact_size_bytes: int = DEFAULT_MAX_EXPORTED_VIDEO_SIZE_BYTES,
    max_non_video_input_size_bytes: int = MAX_EXPORT_ARTIFACT_SIZE_BYTES,
    max_video_input_size_bytes: int = DEFAULT_MAX_EXPORTED_VIDEO_INPUT_SIZE_BYTES,
) -> bytes:
    zip_buffer = BytesIO()
    write_campaign_export_zip(
        campaign=campaign,
        storage=storage,
        destination=zip_buffer,
        max_video_artifact_size_bytes=max_video_artifact_size_bytes,
        max_non_video_input_size_bytes=max_non_video_input_size_bytes,
        max_video_input_size_bytes=max_video_input_size_bytes,
    )
    return zip_buffer.getvalue()


@router.get("", response_model=list[CampaignRead])
def list_campaigns(
    db: Session = Depends(get_db),
    offset: int = 0,
    limit: int = 50,
) -> list[Campaign]:
    statement = (
        select(Campaign)
        .order_by(Campaign.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(db.scalars(statement).all())


@router.post("", response_model=CampaignRead, status_code=status.HTTP_201_CREATED)
def create_campaign(
    campaign_in: CampaignCreate,
    db: Session = Depends(get_db),
) -> Campaign:
    campaign = Campaign(**campaign_in.model_dump())
    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    return campaign


def remove_temporary_export(path: str) -> None:
    Path(path).unlink(missing_ok=True)


@router.get("/{campaign_id}/export")
def export_campaign_pack(
    campaign_id: uuid.UUID,
    db: Session = Depends(get_db),
    storage: B2StorageService = Depends(get_storage_service),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    campaign = get_campaign_with_assets_or_404(campaign_id, db)
    with NamedTemporaryFile(
        mode="w+b",
        prefix="sereneset-campaign-export-",
        suffix=".zip",
        delete=False,
    ) as temporary_export:
        export_path = temporary_export.name

    try:
        write_campaign_export_zip(
            campaign=campaign,
            storage=storage,
            destination=export_path,
            max_video_artifact_size_bytes=(
                settings.max_generated_video_size_bytes
            ),
            max_non_video_input_size_bytes=(
                settings.max_video_source_image_size_bytes
            ),
            max_video_input_size_bytes=(
                settings.max_video_source_video_size_bytes
            ),
        )
    except (
        StorageConfigurationError,
        StorageOperationError,
        BotoCoreError,
        ClientError,
    ) as exc:
        remove_temporary_export(export_path)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Campaign export could not be created because B2 storage failed",
        ) from exc
    except OSError as exc:
        remove_temporary_export(export_path)
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail="Campaign export could not be written to temporary storage",
        ) from exc
    except Exception:
        remove_temporary_export(export_path)
        raise

    filename = f"{slugify_filename(campaign.name)}-export.zip"

    return FileResponse(
        path=export_path,
        media_type="application/zip",
        filename=filename,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
        background=BackgroundTask(remove_temporary_export, export_path),
    )


@router.get("/{campaign_id}", response_model=CampaignRead)
def get_campaign(
    campaign_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> Campaign:
    return get_campaign_or_404(campaign_id, db)


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_campaign(
    campaign_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> Response:
    campaign = get_campaign_or_404(campaign_id, db)
    db.delete(campaign)
    db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{campaign_id}", response_model=CampaignRead)
def update_campaign(
    campaign_id: uuid.UUID,
    campaign_in: CampaignUpdate,
    db: Session = Depends(get_db),
) -> Campaign:
    campaign = get_campaign_or_404(campaign_id, db)

    update_data = campaign_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(campaign, field, value)

    db.commit()
    db.refresh(campaign)

    return campaign
