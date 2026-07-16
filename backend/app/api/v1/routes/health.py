import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models.worker_heartbeat import (
    VIDEO_GENERATION_WORKER,
    WorkerHeartbeat,
)
from app.services.storage import (
    B2StorageService,
    get_readiness_storage_service,
)


router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


def utc_now() -> datetime:
    return datetime.now(UTC)


def worker_readiness_check(
    db: Session,
    *,
    checked_at: datetime,
    stale_after_seconds: int,
) -> dict[str, Any]:
    heartbeat = db.get(WorkerHeartbeat, VIDEO_GENERATION_WORKER)
    if heartbeat is None:
        return {
            "status": "unavailable",
            "detail": "No video worker heartbeat has been recorded",
            "stale_after_seconds": stale_after_seconds,
        }

    heartbeat_at = heartbeat.heartbeat_at
    if heartbeat_at.tzinfo is None:
        heartbeat_at = heartbeat_at.replace(tzinfo=UTC)

    age_seconds = max(0.0, (checked_at - heartbeat_at).total_seconds())
    worker_status = (
        "ok" if age_seconds <= stale_after_seconds else "stale"
    )
    return {
        "status": worker_status,
        "last_heartbeat_at": heartbeat_at.isoformat(),
        "age_seconds": round(age_seconds, 3),
        "stale_after_seconds": stale_after_seconds,
    }


@router.get("/health/ready")
def readiness_check(
    db: Session = Depends(get_db),
    storage: B2StorageService = Depends(get_readiness_storage_service),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    checked_at = utc_now()
    checks: dict[str, dict[str, Any]] = {}

    postgresql_ready = False
    try:
        db.execute(text("SELECT 1")).scalar_one()
        checks["postgresql"] = {"status": "ok"}
        postgresql_ready = True
    except Exception:
        logger.warning("PostgreSQL readiness check failed", exc_info=True)
        checks["postgresql"] = {
            "status": "unavailable",
            "detail": "PostgreSQL is unavailable",
        }

    try:
        storage.check_bucket_access()
        checks["b2"] = {"status": "ok"}
    except Exception:
        logger.warning("B2 readiness check failed", exc_info=True)
        checks["b2"] = {
            "status": "unavailable",
            "detail": "Backblaze B2 is unavailable",
        }

    if postgresql_ready:
        try:
            checks["worker"] = worker_readiness_check(
                db,
                checked_at=checked_at,
                stale_after_seconds=(
                    settings.worker_heartbeat_stale_after_seconds
                ),
            )
        except Exception:
            logger.warning("Worker readiness check failed", exc_info=True)
            checks["worker"] = {
                "status": "unavailable",
                "detail": "Worker heartbeat could not be read",
            }
    else:
        checks["worker"] = {
            "status": "unavailable",
            "detail": "Worker heartbeat requires PostgreSQL",
        }

    ready = all(check["status"] == "ok" for check in checks.values())
    return JSONResponse(
        status_code=(
            status.HTTP_200_OK
            if ready
            else status.HTTP_503_SERVICE_UNAVAILABLE
        ),
        content={
            "status": "ready" if ready else "not_ready",
            "checked_at": checked_at.isoformat(),
            "checks": checks,
        },
    )
