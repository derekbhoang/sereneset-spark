import json
import re
import uuid
from datetime import UTC, datetime
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.asset import Asset, AssetVersion, ReviewStatus
from app.models.campaign import Campaign
from app.schemas.campaign import CampaignCreate, CampaignRead, CampaignUpdate
from app.services.storage import (
    B2StorageService,
    StorageConfigurationError,
    get_storage_service,
    normalize_artifact_filename,
)


router = APIRouter(prefix="/campaigns", tags=["campaigns"])


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
        .options(selectinload(Campaign.assets).selectinload(Asset.versions))
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


def build_campaign_export_manifest(
    *,
    campaign: Campaign,
    approved_assets: list[Asset],
    metadata_paths: dict[uuid.UUID, str],
    artifact_paths: dict[uuid.UUID, str],
    artifact_export_errors: dict[uuid.UUID, str],
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
                        "artifact_storage_key": version.artifact_storage_key,
                        "artifact_filename": version.artifact_filename,
                        "artifact_content_type": version.artifact_content_type,
                        "artifact_size_bytes": version.artifact_size_bytes,
                        "artifact_zip_path": artifact_paths.get(version.id),
                        "artifact_export_error": artifact_export_errors.get(
                            version.id
                        ),
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
            "generation_metadata": version.generation_metadata,
        },
        "exported_at": datetime.now(UTC).isoformat(),
    }


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
    artifact_paths: dict[uuid.UUID, str] = {}
    artifact_export_errors: dict[uuid.UUID, str] = {}
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
                export_zip.writestr(
                    metadata_path,
                    json.dumps(
                        build_version_metadata_sidecar(
                            campaign=campaign,
                            asset=asset,
                            version=version,
                        ),
                        indent=2,
                        ensure_ascii=False,
                    ),
                )

                if version.artifact_storage_key is None:
                    continue

                artifact_filename = normalize_artifact_filename(
                    version.artifact_filename or f"artifact-{version.id}",
                )
                artifact_path = f"artifacts/{base_path}/{artifact_filename}"
                try:
                    export_zip.writestr(
                        artifact_path,
                        storage.download_bytes(key=version.artifact_storage_key),
                    )
                    artifact_paths[version.id] = artifact_path
                except (StorageConfigurationError, BotoCoreError, ClientError):
                    artifact_export_errors[version.id] = (
                        "Artifact could not be downloaded from B2 during export"
                    )

        manifest = build_campaign_export_manifest(
            campaign=campaign,
            approved_assets=approved_assets,
            metadata_paths=metadata_paths,
            artifact_paths=artifact_paths,
            artifact_export_errors=artifact_export_errors,
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
