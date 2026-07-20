import subprocess
import sys
import unittest
from unittest.mock import call, patch

from scripts.release import run_release


class ReleaseTests(unittest.TestCase):
    def test_runs_migration_before_showcase_seed(self) -> None:
        with patch("scripts.release.subprocess.run") as run:
            run_release()

        self.assertEqual(
            run.call_args_list,
            [
                call(
                    [sys.executable, "-m", "alembic", "upgrade", "head"],
                    check=True,
                ),
                call(
                    [sys.executable, "scripts/seed.py", "--showcase-only"],
                    check=True,
                ),
            ],
        )

    def test_does_not_seed_when_migration_fails(self) -> None:
        error = subprocess.CalledProcessError(1, "alembic")

        with patch("scripts.release.subprocess.run", side_effect=error) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                run_release()

        run.assert_called_once_with(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
