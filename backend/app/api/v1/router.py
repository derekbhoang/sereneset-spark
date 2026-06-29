from fastapi import APIRouter
from app.api.v1.routes.assets import router as assets_router
from app.api.v1.routes.campaigns import router as campaigns_router
from app.api.v1.routes.health import router as health_router


router = APIRouter()


router.include_router(health_router)
router.include_router(campaigns_router)
router.include_router(assets_router)
