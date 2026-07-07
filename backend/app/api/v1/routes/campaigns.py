import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import PurePosixPath
from urllib.error import URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen
from zipfile import ZIP_DEFLATED, ZipFile

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.asset import Asset, AssetVersion, AssetVersionInput, ReviewStatus
from app.models.campaign import Campaign
from app.schemas.campaign import CampaignCreate, CampaignRead, CampaignUpdate
from app.services.storage import (
    B2StorageService,
    StorageConfigurationError,
    get_storage_service,
    normalize_artifact_filename,
)


router = APIRouter(prefix="/campaigns", tags=["campaigns"])
MAX_EXPORT_ARTIFACT_SIZE_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class ExportArtifactReference:
    storage_key: str | None
    url: str | None
    filename: str | None
    content_type: str | None
    size_bytes: int | None
    source: str


@dataclass(frozen=True)
class ExportInputReference:
    id: uuid.UUID | None
    role: str | None
    storage_key: str | None
    url: str | None
    filename: str | None
    content_type: str | None
    size_bytes: int | None
    sha256: str | None
    created_at: str | None
    source: str


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
            .selectinload(AssetVersion.inputs)
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


def serialize_asset_version_input(
    version_input: AssetVersionInput,
) -> dict[str, object]:
    return {
        "id": str(version_input.id),
        "role": version_input.role,
        "storage_key": version_input.storage_key,
        "filename": version_input.filename,
        "content_type": version_input.content_type,
        "size_bytes": version_input.size_bytes,
        "sha256": version_input.sha256,
        "created_at": version_input.created_at.isoformat(),
    }


def export_input_record(
    reference: ExportInputReference,
    *,
    zip_path: str | None = None,
    export_error: str | None = None,
) -> dict[str, object]:
    return {
        "id": str(reference.id) if reference.id is not None else None,
        "role": reference.role,
        "storage_key": reference.storage_key,
        "url": reference.url,
        "filename": reference.filename,
        "content_type": reference.content_type,
        "size_bytes": reference.size_bytes,
        "sha256": reference.sha256,
        "created_at": reference.created_at,
        "metadata_source": reference.source,
        "zip_path": zip_path,
        "export_error": export_error,
    }


def build_campaign_export_manifest(
    *,
    campaign: Campaign,
    approved_assets: list[Asset],
    metadata_paths: dict[uuid.UUID, str],
    metadata_sources: dict[uuid.UUID, str],
    metadata_export_errors: dict[uuid.UUID, str],
    artifact_paths: dict[uuid.UUID, str],
    artifact_sources: dict[uuid.UUID, str],
    artifact_export_errors: dict[uuid.UUID, str],
    input_exports: dict[uuid.UUID, list[dict[str, object]]],
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
                        "artifact_export_error": artifact_export_errors.get(
                            version.id
                        ),
                        "input_assets": input_exports.get(version.id, []),
                        "generation_metadata": version.generation_metadata,
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
            "generation_metadata": version.generation_metadata,
        },
        "exported_at": datetime.now(UTC).isoformat(),
    }


def encode_pretty_json(data: dict[str, object]) -> bytes:
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


def asset_version_input_reference(
    version_input: AssetVersionInput,
) -> ExportInputReference:
    return ExportInputReference(
        id=version_input.id,
        role=version_input.role,
        storage_key=version_input.storage_key,
        url=None,
        filename=version_input.filename,
        content_type=version_input.content_type,
        size_bytes=version_input.size_bytes,
        sha256=version_input.sha256,
        created_at=version_input.created_at.isoformat(),
        source="asset_version_input",
    )


def metadata_input_asset_references(
    version: AssetVersion,
) -> list[ExportInputReference]:
    metadata = version.generation_metadata or {}
    provenance = optional_metadata_dict(metadata.get("provenance"))
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
                    size_bytes=optional_int(input_item.get("size_bytes")),
                    sha256=optional_string(input_item.get("sha256")),
                    created_at=optional_string(input_item.get("created_at")),
                    source=source_name,
                )
            )

    return references


def input_reference_identity(reference: ExportInputReference) -> tuple[str, str]:
    if reference.id is not None:
        return ("id", str(reference.id))

    if reference.storage_key:
        return ("storage_key", reference.storage_key)

    if reference.url:
        return ("url", reference.url)

    return ("filename", reference.filename or "")


def version_input_references(version: AssetVersion) -> list[ExportInputReference]:
    references: list[ExportInputReference] = []
    seen: set[tuple[str, str]] = set()

    for version_input in sorted(version.inputs, key=lambda item: item.created_at):
        reference = asset_version_input_reference(version_input)
        seen.add(input_reference_identity(reference))
        if reference.storage_key or reference.url:
            references.append(reference)

    for reference in metadata_input_asset_references(version):
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
                source="genblaze_source_artifact",
            )
        )

    provenance = optional_metadata_dict(metadata.get("provenance"))
    for source_name, asset_items in (
        ("generation_metadata_asset", optional_asset_metadata_list(metadata.get("assets"))),
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
        return ExportArtifactReference(
            storage_key=version.artifact_storage_key,
            url=None,
            filename=version.artifact_filename,
            content_type=version.artifact_content_type,
            size_bytes=version.artifact_size_bytes,
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


def download_url_bytes(url: str) -> bytes:
    request = Request(
        url,
        headers={"User-Agent": "SereneSet-Spark/1.0"},
    )

    with urlopen(request, timeout=60) as response:
        body = response.read(MAX_EXPORT_ARTIFACT_SIZE_BYTES + 1)

    if not body:
        raise ValueError("Generated artifact URL returned an empty body")

    if len(body) > MAX_EXPORT_ARTIFACT_SIZE_BYTES:
        raise ValueError("Generated artifact is larger than 25 MB")

    return body


def download_artifact_bytes(
    *,
    storage: B2StorageService,
    reference: ExportArtifactReference,
) -> bytes:
    if reference.storage_key:
        return storage.download_bytes(key=reference.storage_key)

    if reference.url:
        return download_url_bytes(reference.url)

    raise ValueError("Artifact reference did not include a B2 key or URL")


def download_input_bytes(
    *,
    storage: B2StorageService,
    reference: ExportInputReference,
) -> bytes:
    if reference.storage_key:
        return storage.download_bytes(key=reference.storage_key)

    if reference.url:
        return download_url_bytes(reference.url)

    raise ValueError("Input reference did not include a B2 key or URL")


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


def make_campaign_export_zip(
    *,
    campaign: Campaign,
    storage: B2StorageService,
) -> bytes:
    approved_assets = sorted(
        (
            asset
            for asset in campaign.assets
            if asset.status == ReviewStatus.approved
        ),
        key=lambda asset: (asset.channel, asset.title),
    )
    metadata_paths: dict[uuid.UUID, str] = {}
    metadata_sources: dict[uuid.UUID, str] = {}
    metadata_export_errors: dict[uuid.UUID, str] = {}
    artifact_paths: dict[uuid.UUID, str] = {}
    artifact_sources: dict[uuid.UUID, str] = {}
    artifact_export_errors: dict[uuid.UUID, str] = {}
    input_exports: dict[uuid.UUID, list[dict[str, object]]] = {}
    used_input_paths: set[str] = set()
    zip_buffer = BytesIO()

    with ZipFile(zip_buffer, mode="w", compression=ZIP_DEFLATED) as export_zip:
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
                        storage.download_bytes(key=version.storage_key),
                    )
                    metadata_sources[version.id] = "b2_sidecar"
                except (StorageConfigurationError, BotoCoreError, ClientError):
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
                        export_zip.writestr(
                            artifact_path,
                            download_artifact_bytes(
                                storage=storage,
                                reference=artifact_reference,
                            ),
                        )
                        artifact_paths[version.id] = artifact_path
                        artifact_sources[version.id] = artifact_reference.source
                    except (
                        StorageConfigurationError,
                        BotoCoreError,
                        ClientError,
                        OSError,
                        URLError,
                        ValueError,
                    ):
                        artifact_export_errors[version.id] = (
                            "Artifact could not be downloaded from its source "
                            "during export"
                        )

                for input_reference in version_input_references(version):
                    input_path = make_input_zip_path(
                        asset=asset,
                        version=version,
                        reference=input_reference,
                        used_paths=used_input_paths,
                    )
                    try:
                        export_zip.writestr(
                            input_path,
                            download_input_bytes(
                                storage=storage,
                                reference=input_reference,
                            ),
                        )
                        input_exports.setdefault(version.id, []).append(
                            export_input_record(
                                input_reference,
                                zip_path=input_path,
                            )
                        )
                    except (
                        StorageConfigurationError,
                        BotoCoreError,
                        ClientError,
                        OSError,
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
            approved_assets=approved_assets,
            metadata_paths=metadata_paths,
            metadata_sources=metadata_sources,
            metadata_export_errors=metadata_export_errors,
            artifact_paths=artifact_paths,
            artifact_sources=artifact_sources,
            artifact_export_errors=artifact_export_errors,
            input_exports=input_exports,
        )
        export_zip.writestr(
            "manifest.json",
            json.dumps(manifest, indent=2, ensure_ascii=False),
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


@router.get("/{campaign_id}/export")
def export_campaign_pack(
    campaign_id: uuid.UUID,
    db: Session = Depends(get_db),
    storage: B2StorageService = Depends(get_storage_service),
) -> Response:
    campaign = get_campaign_with_assets_or_404(campaign_id, db)

    try:
        export_body = make_campaign_export_zip(
            campaign=campaign,
            storage=storage,
        )
    except (StorageConfigurationError, BotoCoreError, ClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Campaign export could not be created because B2 storage failed",
        ) from exc

    filename = f"{slugify_filename(campaign.name)}-export.zip"

    return Response(
        content=export_body,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
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
