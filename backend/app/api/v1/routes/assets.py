import uuid
from datetime import UTC, datetime

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.asset import Asset, AssetVersion, ReviewStatus
from app.models.campaign import Campaign
from app.schemas.asset import (
    AssetCreate,
    AssetRead,
    AssetStatusUpdate,
    AssetVersionCreate,
    AssetVersionDownloadUrl,
    AssetVersionRead,
)
from app.services.storage import (
    B2StorageService,
    StorageConfigurationError,
    build_asset_version_storage_key,
    get_storage_service,
)


router = APIRouter(tags=["assets"])


def get_asset_or_404(asset_id: uuid.UUID, db: Session) -> Asset:
    statement = (
        select(Asset)
        .options(selectinload(Asset.versions))
        .where(Asset.id == asset_id)
    )
    asset = db.scalar(statement)
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    return asset


def get_asset_version_or_404(
    *,
    asset_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session,
) -> AssetVersion:
    statement = select(AssetVersion).where(
        AssetVersion.id == version_id,
        AssetVersion.asset_id == asset_id,
    )
    version = db.scalar(statement)
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset version not found",
        )

    return version


def ensure_campaign_exists(campaign_id: uuid.UUID, db: Session) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )

    return campaign


def make_asset_version(
    *,
    asset: Asset,
    version_in: AssetVersionCreate,
) -> AssetVersion:
    version_data = version_in.model_dump()
    storage_key = build_asset_version_storage_key(
        campaign_id=asset.campaign_id,
        asset_id=asset.id,
        version_number=version_in.version_number,
    )

    return AssetVersion(
        asset_id=asset.id,
        storage_key=storage_key,
        **version_data,
    )


def build_asset_version_sidecar(
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
            "generation_metadata": version.generation_metadata,
        },
        "stored_at": datetime.now(UTC).isoformat(),
    }


def upload_asset_version_sidecar(
    *,
    storage: B2StorageService,
    campaign: Campaign,
    asset: Asset,
    version: AssetVersion,
) -> None:
    stored_object = storage.upload_json(
        key=version.storage_key,
        data=build_asset_version_sidecar(
            campaign=campaign,
            asset=asset,
            version=version,
        ),
        metadata={
            "campaign_id": str(campaign.id),
            "asset_id": str(asset.id),
            "version_number": version.version_number,
            "content_kind": "asset-version-sidecar",
        },
    )
    version.storage_key = stored_object.key


@router.get("/campaigns/{campaign_id}/assets", response_model=list[AssetRead])
def list_campaign_assets(
    campaign_id: uuid.UUID,
    db: Session = Depends(get_db),
    status_filter: ReviewStatus | None = Query(default=None, alias="status"),
    channel: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> list[Asset]:
    ensure_campaign_exists(campaign_id, db)

    statement = (
        select(Asset)
        .options(selectinload(Asset.versions))
        .where(Asset.campaign_id == campaign_id)
        .order_by(Asset.updated_at.desc())
        .offset(offset)
        .limit(limit)
    )

    if status_filter is not None:
        statement = statement.where(Asset.status == status_filter)

    if channel is not None:
        statement = statement.where(Asset.channel == channel)

    return list(db.scalars(statement).all())


@router.post(
    "/campaigns/{campaign_id}/assets",
    response_model=AssetRead,
    status_code=status.HTTP_201_CREATED,
)
def create_campaign_asset(
    campaign_id: uuid.UUID,
    asset_in: AssetCreate,
    db: Session = Depends(get_db),
    storage: B2StorageService = Depends(get_storage_service),
) -> Asset:
    campaign = ensure_campaign_exists(campaign_id, db)

    asset_data = asset_in.model_dump(exclude={"initial_version"})
    asset = Asset(campaign_id=campaign_id, **asset_data)

    try:
        db.add(asset)
        db.flush()

        if asset_in.initial_version is not None:
            version = make_asset_version(
                asset=asset,
                version_in=asset_in.initial_version,
            )
            db.add(version)
            db.flush()
            upload_asset_version_sidecar(
                storage=storage,
                campaign=campaign,
                asset=asset,
                version=version,
            )

        db.commit()
    except (StorageConfigurationError, BotoCoreError, ClientError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Asset was not created because B2 storage failed: {exc}",
        ) from exc

    db.refresh(asset)

    return get_asset_or_404(asset.id, db)


@router.get("/assets/{asset_id}", response_model=AssetRead)
def get_asset(
    asset_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> Asset:
    return get_asset_or_404(asset_id, db)


@router.patch("/assets/{asset_id}/status", response_model=AssetRead)
def update_asset_status(
    asset_id: uuid.UUID,
    status_in: AssetStatusUpdate,
    db: Session = Depends(get_db),
) -> Asset:
    asset = get_asset_or_404(asset_id, db)
    asset.status = status_in.status

    db.commit()
    db.refresh(asset)

    return get_asset_or_404(asset.id, db)


@router.get("/assets/{asset_id}/versions", response_model=list[AssetVersionRead])
def list_asset_versions(
    asset_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> list[AssetVersion]:
    get_asset_or_404(asset_id, db)

    statement = (
        select(AssetVersion)
        .where(AssetVersion.asset_id == asset_id)
        .order_by(AssetVersion.version_number.desc())
    )
    return list(db.scalars(statement).all())


@router.get(
    "/assets/{asset_id}/versions/{version_id}/download-url",
    response_model=AssetVersionDownloadUrl,
)
def get_asset_version_download_url(
    asset_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    storage: B2StorageService = Depends(get_storage_service),
    expires_seconds: int = Query(default=3600, ge=60, le=604800),
) -> AssetVersionDownloadUrl:
    version = get_asset_version_or_404(
        asset_id=asset_id,
        version_id=version_id,
        db=db,
    )

    try:
        download_url = storage.generate_presigned_download_url(
            key=version.storage_key,
            expires_seconds=expires_seconds,
        )
    except (StorageConfigurationError, BotoCoreError, ClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Download URL could not be created because B2 storage failed",
        ) from exc

    return AssetVersionDownloadUrl(
        asset_id=asset_id,
        version_id=version.id,
        storage_key=version.storage_key,
        download_url=download_url,
        expires_seconds=expires_seconds,
    )


@router.post(
    "/assets/{asset_id}/versions",
    response_model=AssetVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_asset_version(
    asset_id: uuid.UUID,
    version_in: AssetVersionCreate,
    db: Session = Depends(get_db),
    storage: B2StorageService = Depends(get_storage_service),
) -> AssetVersion:
    asset = get_asset_or_404(asset_id, db)
    campaign = ensure_campaign_exists(asset.campaign_id, db)

    version = make_asset_version(asset=asset, version_in=version_in)

    try:
        db.add(version)
        db.flush()
        upload_asset_version_sidecar(
            storage=storage,
            campaign=campaign,
            asset=asset,
            version=version,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Asset version number already exists",
        ) from exc
    except (StorageConfigurationError, BotoCoreError, ClientError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Asset version was not created because B2 storage failed",
        ) from exc

    db.refresh(version)

    return version
