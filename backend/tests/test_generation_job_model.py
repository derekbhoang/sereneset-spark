import unittest

from sqlalchemy import CheckConstraint, UniqueConstraint

from app.models.asset import AssetVersion
from app.models.generation_job import (
    GenerationJob,
    GenerationJobKind,
    GenerationJobStatus,
)


class GenerationJobModelTests(unittest.TestCase):
    def test_job_states_are_stable_public_values(self) -> None:
        self.assertEqual(
            [status.value for status in GenerationJobStatus],
            ["queued", "running", "succeeded", "failed", "canceled"],
        )
        self.assertEqual(GenerationJobKind.video.value, "video")

    def test_table_contains_job_lifecycle_constraints(self) -> None:
        table = GenerationJob.__table__
        constraint_names = {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }

        self.assertEqual(table.name, "generation_jobs")
        unique_constraint_names = {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }

        self.assertEqual(
            unique_constraint_names,
            {"uq_generation_job_asset_version"},
        )
        self.assertIn(
            "ix_generation_jobs_status_created_at",
            {index.name for index in table.indexes},
        )
        self.assertEqual(
            constraint_names,
            {
                "ck_generation_jobs_kind",
                "ck_generation_jobs_status",
                "ck_generation_jobs_progress_percent",
                "ck_generation_jobs_attempt_count",
            },
        )

    def test_asset_version_owns_one_generation_job(self) -> None:
        relationship = AssetVersion.__mapper__.relationships["generation_job"]

        self.assertFalse(relationship.uselist)
        self.assertIn("delete-orphan", relationship.cascade)


if __name__ == "__main__":
    unittest.main()
