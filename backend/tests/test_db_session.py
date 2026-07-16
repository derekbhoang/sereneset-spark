import unittest
from unittest.mock import MagicMock, patch

from app.core.config import Settings
from app.db.session import create_db_engine


class DatabaseEngineTests(unittest.TestCase):
    def test_uses_bounded_managed_postgresql_pool(self) -> None:
        settings = Settings(
            _env_file=None,
            DATABASE_POOL_SIZE=4,
            DATABASE_MAX_OVERFLOW=3,
            DATABASE_POOL_TIMEOUT_SECONDS=20,
            DATABASE_POOL_RECYCLE_SECONDS=600,
            DATABASE_CONNECT_TIMEOUT_SECONDS=8,
        )
        expected_engine = MagicMock()

        with patch(
            "app.db.session.create_engine",
            return_value=expected_engine,
        ) as create_engine:
            engine = create_db_engine(settings)

        self.assertIs(engine, expected_engine)
        create_engine.assert_called_once_with(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=4,
            max_overflow=3,
            pool_timeout=20,
            pool_recycle=600,
            pool_use_lifo=True,
            connect_args={"connect_timeout": 8},
        )


if __name__ == "__main__":
    unittest.main()
