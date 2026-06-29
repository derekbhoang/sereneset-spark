import uuid

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
    AssetVersionRead,
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


def ensure_campaign_exists(campaign_id: uuid.UUID, db: Session) -> None:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )


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
) -> Asset:
    ensure_campaign_exists(campaign_id, db)

    asset_data = asset_in.model_dump(exclude={"initial_version"})
    asset = Asset(campaign_id=campaign_id, **asset_data)

    if asset_in.initial_version is not None:
        version = AssetVersion(**asset_in.initial_version.model_dump())
        asset.versions.append(version)

    db.add(asset)
    db.commit()
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


@router.post(
    "/assets/{asset_id}/versions",
    response_model=AssetVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_asset_version(
    asset_id: uuid.UUID,
    version_in: AssetVersionCreate,
    db: Session = Depends(get_db),
) -> AssetVersion:
    get_asset_or_404(asset_id, db)

    version = AssetVersion(asset_id=asset_id, **version_in.model_dump())
    db.add(version)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Asset version number already exists",
        ) from exc

    db.refresh(version)

    return version
