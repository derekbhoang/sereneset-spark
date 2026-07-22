import unittest
import uuid

from sqlalchemy import CheckConstraint

from app.models.asset import AssetInputMediaKind, AssetVersionInput
from app.services.input_provenance import (
    build_asset_version_input,
    infer_input_media_kind,
)


class InputProvenanceTests(unittest.TestCase):
    def test_model_has_media_and_source_snapshot_constraints(self) -> None:
        table = AssetVersionInput.__table__
        constraint_names = {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }

        self.assertFalse(table.c.media_kind.nullable)
        self.assertTrue(table.c.sha256.nullable)
        self.assertFalse(table.c.source_asset_id.foreign_keys)
        self.assertFalse(table.c.source_version_id.foreign_keys)
        self.assertEqual(
            constraint_names,
            {
                "ck_asset_version_inputs_media_kind",
                "ck_asset_version_inputs_source_version_snapshot",
            },
        )

    def test_classifies_supported_provenance_media(self) -> None:
        expectations = {
            "image/webp": AssetInputMediaKind.image,
            "video/mp4": AssetInputMediaKind.video,
            "text/markdown": AssetInputMediaKind.document,
            "application/pdf": AssetInputMediaKind.document,
            "application/octet-stream": AssetInputMediaKind.other,
        }

        for content_type, expected_kind in expectations.items():
            with self.subTest(content_type=content_type):
                self.assertEqual(infer_input_media_kind(content_type), expected_kind)

    def test_builds_immutable_source_version_snapshot_without_a_hash(self) -> None:
        source_asset_id = uuid.uuid4()
        source_version_id = uuid.uuid4()

        version_input = build_asset_version_input(
            asset_version_id=uuid.uuid4(),
            record={
                "role": "source_creative",
                "storage_key": "campaigns/source/artifact/source.mp4",
                "filename": "source.mp4",
                "content_type": "video/mp4",
                "size_bytes": 4096,
                "sha256": None,
                "source": "source_version_artifact",
                "storage_ownership": "source_asset_version",
                "source_asset_id": str(source_asset_id),
                "source_version_id": str(source_version_id),
                "source_version_number": 4,
            },
        )

        self.assertEqual(version_input.media_kind, AssetInputMediaKind.video.value)
        self.assertIsNone(version_input.sha256)
        self.assertEqual(version_input.source_asset_id, source_asset_id)
        self.assertEqual(version_input.source_version_id, source_version_id)
        self.assertEqual(version_input.source_version_number, 4)

    def test_rejects_partial_source_version_snapshot(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires source_asset_id"):
            build_asset_version_input(
                asset_version_id=uuid.uuid4(),
                record={
                    "role": "source_creative",
                    "storage_key": "campaigns/source/artifact/source.mp4",
                    "filename": "source.mp4",
                    "content_type": "video/mp4",
                    "size_bytes": 4096,
                    "source_version_id": str(uuid.uuid4()),
                },
            )


if __name__ == "__main__":
    unittest.main()
