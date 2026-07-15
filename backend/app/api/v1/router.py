from fastapi import APIRouter
from app.api.v1.routes.assets import router as assets_router
from app.api.v1.routes.brand_assets import router as brand_assets_router
from app.api.v1.routes.campaign_brand_assets import (
    router as campaign_brand_assets_router,
)
from app.api.v1.routes.campaigns import router as campaigns_router
from app.api.v1.routes.generation_jobs import router as generation_jobs_router
from app.api.v1.routes.health import router as health_router


router = APIRouter()


router.include_router(health_router)
router.include_router(campaigns_router)
router.include_router(assets_router)
router.include_router(brand_assets_router)
router.include_router(campaign_brand_assets_router)
router.include_router(generation_jobs_router)
