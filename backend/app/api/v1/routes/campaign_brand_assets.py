import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.brand_asset import BrandAsset, CampaignBrandAsset
from app.models.campaign import Campaign
from app.schemas.brand_asset import (
    CampaignBrandAssetCreate,
    CampaignBrandAssetRead,
)


router = APIRouter(
    prefix="/campaigns/{campaign_id}/brand-assets",
    tags=["campaign-brand-assets"],
)


def ensure_campaign_exists(campaign_id: uuid.UUID, db: Session) -> None:
    if db.get(Campaign, campaign_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )


def get_brand_asset_or_404(
    brand_asset_id: uuid.UUID,
    db: Session,
) -> BrandAsset:
    brand_asset = db.get(BrandAsset, brand_asset_id)
    if brand_asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Brand asset not found",
        )

    return brand_asset


def get_campaign_brand_asset_link_or_404(
    *,
    campaign_id: uuid.UUID,
    link_id: uuid.UUID,
    db: Session,
) -> CampaignBrandAsset:
    statement = (
        select(CampaignBrandAsset)
        .options(selectinload(CampaignBrandAsset.brand_asset))
        .where(
            CampaignBrandAsset.id == link_id,
            CampaignBrandAsset.campaign_id == campaign_id,
        )
    )
    link = db.scalar(statement)
    if link is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign brand asset attachment not found",
        )

    return link


@router.get("", response_model=list[CampaignBrandAssetRead])
def list_campaign_brand_assets(
    campaign_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> list[CampaignBrandAsset]:
    ensure_campaign_exists(campaign_id, db)

    statement = (
        select(CampaignBrandAsset)
        .options(selectinload(CampaignBrandAsset.brand_asset))
        .where(CampaignBrandAsset.campaign_id == campaign_id)
        .order_by(CampaignBrandAsset.created_at.desc())
    )
    return list(db.scalars(statement).all())


@router.post(
    "",
    response_model=CampaignBrandAssetRead,
    status_code=status.HTTP_201_CREATED,
)
def attach_brand_asset_to_campaign(
    campaign_id: uuid.UUID,
    attachment_in: CampaignBrandAssetCreate,
    db: Session = Depends(get_db),
) -> CampaignBrandAsset:
    ensure_campaign_exists(campaign_id, db)
    brand_asset = get_brand_asset_or_404(attachment_in.brand_asset_id, db)

    if not brand_asset.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Archived brand assets cannot be attached to campaigns",
        )

    link = CampaignBrandAsset(
        campaign_id=campaign_id,
        brand_asset_id=brand_asset.id,
        role=attachment_in.role,
    )
    db.add(link)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Brand asset is already attached to this campaign "
                "with that role"
            ),
        ) from exc

    return get_campaign_brand_asset_link_or_404(
        campaign_id=campaign_id,
        link_id=link.id,
        db=db,
    )


@router.delete(
    "/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def detach_brand_asset_from_campaign(
    campaign_id: uuid.UUID,
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> Response:
    link = get_campaign_brand_asset_link_or_404(
        campaign_id=campaign_id,
        link_id=link_id,
        db=db,
    )
    db.delete(link)
    db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
