import unittest
from datetime import UTC, datetime
from threading import Event
from unittest.mock import MagicMock, patch

from sqlalchemy.dialects import postgresql

from app.models.worker_heartbeat import (
    VIDEO_GENERATION_WORKER,
    WorkerHeartbeat,
)
from app.services.worker_heartbeat import (
    record_worker_heartbeat,
    run_worker_heartbeat_loop,
)


class WorkerHeartbeatTests(unittest.TestCase):
    def test_model_uses_worker_name_as_primary_key(self) -> None:
        primary_key_columns = {
            column.name for column in WorkerHeartbeat.__table__.primary_key
        }

        self.assertEqual(primary_key_columns, {"worker_name"})

    def test_records_heartbeat_with_postgresql_upsert(self) -> None:
        db = MagicMock()
        heartbeat_at = datetime(2026, 7, 16, 1, 2, 3, tzinfo=UTC)

        recorded_at = record_worker_heartbeat(
            db,
            heartbeat_at=heartbeat_at,
        )

        self.assertEqual(recorded_at, heartbeat_at)
        statement = db.execute.call_args.args[0]
        compiled = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("ON CONFLICT (worker_name) DO UPDATE", compiled)
        self.assertIn("SET heartbeat_at", compiled)
        db.commit.assert_called_once_with()
        db.rollback.assert_not_called()

    def test_rolls_back_failed_heartbeat(self) -> None:
        db = MagicMock()
        db.execute.side_effect = RuntimeError("database unavailable")

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            record_worker_heartbeat(db)

        db.rollback.assert_called_once_with()
        db.commit.assert_not_called()

    def test_heartbeat_loop_stops_cooperatively(self) -> None:
        stop_event = Event()
        session_factory = MagicMock()

        def publish_once(**_kwargs: object) -> None:
            stop_event.set()

        with patch(
            "app.services.worker_heartbeat.publish_worker_heartbeat",
            side_effect=publish_once,
        ) as publish:
            run_worker_heartbeat_loop(
                session_factory=session_factory,
                stop_event=stop_event,
                interval_seconds=10,
                worker_name=VIDEO_GENERATION_WORKER,
            )

        publish.assert_called_once_with(
            session_factory=session_factory,
            worker_name=VIDEO_GENERATION_WORKER,
        )


if __name__ == "__main__":
    unittest.main()
