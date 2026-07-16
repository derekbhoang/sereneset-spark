import logging
from collections.abc import Callable
from datetime import UTC, datetime
from threading import Event

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.worker_heartbeat import (
    VIDEO_GENERATION_WORKER,
    WorkerHeartbeat,
)


logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]


def record_worker_heartbeat(
    db: Session,
    *,
    worker_name: str = VIDEO_GENERATION_WORKER,
    heartbeat_at: datetime | None = None,
) -> datetime:
    recorded_at = heartbeat_at or datetime.now(UTC)
    statement = (
        insert(WorkerHeartbeat)
        .values(
            worker_name=worker_name,
            heartbeat_at=recorded_at,
        )
        .on_conflict_do_update(
            index_elements=[WorkerHeartbeat.worker_name],
            set_={"heartbeat_at": recorded_at},
        )
    )

    try:
        db.execute(statement)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return recorded_at


def publish_worker_heartbeat(
    *,
    session_factory: SessionFactory,
    worker_name: str = VIDEO_GENERATION_WORKER,
) -> None:
    with session_factory() as db:
        record_worker_heartbeat(db, worker_name=worker_name)


def run_worker_heartbeat_loop(
    *,
    session_factory: SessionFactory,
    stop_event: Event,
    interval_seconds: float,
    worker_name: str = VIDEO_GENERATION_WORKER,
) -> None:
    while not stop_event.is_set():
        try:
            publish_worker_heartbeat(
                session_factory=session_factory,
                worker_name=worker_name,
            )
        except Exception:
            logger.exception("Could not publish %s worker heartbeat", worker_name)

        stop_event.wait(interval_seconds)
