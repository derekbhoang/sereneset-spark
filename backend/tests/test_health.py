import json
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from fastapi import status

from app.api.v1.routes.health import health_check, readiness_check
from app.core.config import Settings
from app.models.worker_heartbeat import (
    VIDEO_GENERATION_WORKER,
    WorkerHeartbeat,
)


def response_body(response: object) -> dict[str, object]:
    return json.loads(response.body)


class HealthRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.checked_at = datetime(2026, 7, 16, 2, 0, tzinfo=UTC)
        self.db = MagicMock()
        self.db.execute.return_value.scalar_one.return_value = 1
        self.storage = MagicMock()
        self.settings = Settings(_env_file=None)

    def test_liveness_does_not_probe_dependencies(self) -> None:
        self.assertEqual(health_check(), {"status": "ok"})

    def test_ready_when_all_dependencies_are_healthy(self) -> None:
        self.db.get.return_value = WorkerHeartbeat(
            worker_name=VIDEO_GENERATION_WORKER,
            heartbeat_at=self.checked_at - timedelta(seconds=5),
        )

        with patch(
            "app.api.v1.routes.health.utc_now",
            return_value=self.checked_at,
        ):
            response = readiness_check(
                db=self.db,
                storage=self.storage,
                settings=self.settings,
            )

        body = response_body(response)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["checks"]["postgresql"]["status"], "ok")
        self.assertEqual(body["checks"]["b2"]["status"], "ok")
        self.assertEqual(body["checks"]["worker"]["status"], "ok")
        self.storage.check_bucket_access.assert_called_once_with()

    def test_not_ready_when_worker_heartbeat_is_stale(self) -> None:
        self.db.get.return_value = WorkerHeartbeat(
            worker_name=VIDEO_GENERATION_WORKER,
            heartbeat_at=(
                self.checked_at
                - timedelta(
                    seconds=(
                        self.settings.worker_heartbeat_stale_after_seconds + 1
                    )
                )
            ),
        )

        with patch(
            "app.api.v1.routes.health.utc_now",
            return_value=self.checked_at,
        ):
            response = readiness_check(
                db=self.db,
                storage=self.storage,
                settings=self.settings,
            )

        body = response_body(response)
        self.assertEqual(
            response.status_code,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        self.assertEqual(body["checks"]["worker"]["status"], "stale")

    def test_b2_is_checked_when_postgresql_is_unavailable(self) -> None:
        self.db.execute.side_effect = RuntimeError("database unavailable")

        with patch("app.api.v1.routes.health.logger.warning"):
            response = readiness_check(
                db=self.db,
                storage=self.storage,
                settings=self.settings,
            )

        body = response_body(response)
        self.assertEqual(
            response.status_code,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        self.assertEqual(
            body["checks"]["postgresql"]["status"],
            "unavailable",
        )
        self.assertEqual(body["checks"]["b2"]["status"], "ok")
        self.assertEqual(
            body["checks"]["worker"]["detail"],
            "Worker heartbeat requires PostgreSQL",
        )
        self.db.get.assert_not_called()
        self.storage.check_bucket_access.assert_called_once_with()

    def test_not_ready_when_b2_is_unavailable(self) -> None:
        self.db.get.return_value = WorkerHeartbeat(
            worker_name=VIDEO_GENERATION_WORKER,
            heartbeat_at=self.checked_at,
        )
        self.storage.check_bucket_access.side_effect = RuntimeError(
            "storage unavailable"
        )

        with (
            patch(
                "app.api.v1.routes.health.utc_now",
                return_value=self.checked_at,
            ),
            patch("app.api.v1.routes.health.logger.warning"),
        ):
            response = readiness_check(
                db=self.db,
                storage=self.storage,
                settings=self.settings,
            )

        body = response_body(response)
        self.assertEqual(
            response.status_code,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        self.assertEqual(body["checks"]["b2"]["status"], "unavailable")
        self.assertEqual(body["checks"]["postgresql"]["status"], "ok")
        self.assertEqual(body["checks"]["worker"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
