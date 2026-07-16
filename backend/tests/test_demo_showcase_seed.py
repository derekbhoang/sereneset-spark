import hashlib
import json
import unittest
import uuid
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from zipfile import ZipFile

from app.api.v1.routes.campaigns import make_campaign_export_zip
from app.models.asset import Asset, AssetVersion, AssetVersionInput
from app.models.brand_asset import BrandAsset, CampaignBrandAsset
from app.models.campaign import Campaign
from app.models.generation_job import GenerationJob, GenerationJobStatus
from app.services.storage import StoredObject
from scripts.demo_showcase import (
    IMAGE_V2_ID,
    SHOWCASE_CAMPAIGN_ID,
    VIDEO_JOB_ID,
    VIDEO_V1_ID,
    seed_showcase_campaign,
    stable_demo_id,
)


class FakeSession:
    def __init__(self) -> None:
        self.objects: dict[tuple[type[object], uuid.UUID], object] = {}

    def get(self, model_type: type[object], object_id: uuid.UUID) -> object | None:
        return self.objects.get((model_type, object_id))

    def add(self, model: object) -> None:
        recorded_at = datetime(2026, 7, 16, 9, 30, tzinfo=UTC)
        for field in ("created_at", "updated_at"):
            if hasattr(model, field) and getattr(model, field) is None:
                setattr(model, field, recorded_at)
        self.objects[(type(model), model.id)] = model  # type: ignore[attr-defined]

    def flush(self) -> None:
        return None


class FakeStorage:
    bucket_name = "demo-bucket"

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.upload_count = 0

    def upload_bytes(
        self,
        *,
        key: str,
        body: bytes,
        content_type: str,
        metadata: dict[str, Any] | None = None,
        cache_control: str | None = None,
    ) -> StoredObject:
        self.objects[key] = (body, content_type)
        self.upload_count += 1
        return StoredObject(
            bucket=self.bucket_name,
            key=key,
            content_type=content_type,
            size=len(body),
        )

    def upload_json(
        self,
        *,
        key: str,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> StoredObject:
        return self.upload_bytes(
            key=key,
            body=json.dumps(data, separators=(",", ":")).encode("utf-8"),
            content_type="application/json",
            metadata=metadata,
        )

    def download_bytes(self, *, key: str) -> bytes:
        return self.objects[key][0]

    def iter_download_chunks(
        self,
        *,
        key: str,
        chunk_size_bytes: int = 1024 * 1024,
        max_size_bytes: int | None = None,
    ):
        body = self.objects[key][0]
        if max_size_bytes is not None and len(body) > max_size_bytes:
            raise ValueError("Stored object exceeded the test size limit")
        for offset in range(0, len(body), chunk_size_bytes):
            yield body[offset : offset + chunk_size_bytes]


def wire_seeded_relationships(session: FakeSession) -> Campaign:
    campaign = session.get(Campaign, SHOWCASE_CAMPAIGN_ID)
    assert isinstance(campaign, Campaign)
    brand_assets = {
        item.id: item
        for item in session.objects.values()
        if isinstance(item, BrandAsset)
    }
    links = [
        item
        for item in session.objects.values()
        if isinstance(item, CampaignBrandAsset)
    ]
    for link in links:
        link.brand_asset = brand_assets[link.brand_asset_id]
    campaign.brand_asset_links = links

    assets = [item for item in session.objects.values() if isinstance(item, Asset)]
    versions = [
        item for item in session.objects.values() if isinstance(item, AssetVersion)
    ]
    inputs = [
        item for item in session.objects.values() if isinstance(item, AssetVersionInput)
    ]
    for version in versions:
        version.inputs = [
            item for item in inputs if item.asset_version_id == version.id
        ]
    for asset in assets:
        asset.versions = [
            version for version in versions if version.asset_id == asset.id
        ]
    campaign.assets = assets
    return campaign


class ShowcaseSeedTests(unittest.TestCase):
    def test_stable_demo_ids_are_deterministic_and_scoped(self) -> None:
        self.assertEqual(
            stable_demo_id("asset/example"), stable_demo_id("asset/example")
        )
        self.assertNotEqual(
            stable_demo_id("asset/example"),
            stable_demo_id("version/example"),
        )

    def test_rerun_updates_the_same_rows_and_storage_keys(self) -> None:
        session = FakeSession()
        storage = FakeStorage()

        first = seed_showcase_campaign(session, storage=storage)  # type: ignore[arg-type]
        first_object_count = len(session.objects)
        first_storage_keys = set(storage.objects)
        second = seed_showcase_campaign(session, storage=storage)  # type: ignore[arg-type]

        self.assertEqual(first["campaigns_created"], 1)
        self.assertEqual(first["brand_assets_created"], 3)
        self.assertEqual(first["brand_links_created"], 3)
        self.assertEqual(first["assets_created"], 2)
        self.assertEqual(first["versions_created"], 3)
        self.assertEqual(first["inputs_created"], 8)
        self.assertEqual(first["generation_jobs_created"], 1)
        self.assertEqual(first["storage_objects_uploaded"], 12)

        self.assertEqual(second["campaigns_updated"], 1)
        self.assertEqual(second["brand_assets_updated"], 3)
        self.assertEqual(second["brand_links_updated"], 3)
        self.assertEqual(second["assets_updated"], 2)
        self.assertEqual(second["versions_updated"], 3)
        self.assertEqual(second["inputs_updated"], 8)
        self.assertEqual(second["generation_jobs_updated"], 1)
        self.assertEqual(second["storage_objects_uploaded"], 12)
        self.assertEqual(len(session.objects), first_object_count)
        self.assertEqual(set(storage.objects), first_storage_keys)

        campaign = session.get(Campaign, SHOWCASE_CAMPAIGN_ID)
        self.assertIsNotNone(campaign)
        self.assertEqual(campaign.status, "ready")  # type: ignore[union-attr]

    def test_provenance_matches_the_uploaded_artifact_and_manifest(self) -> None:
        session = FakeSession()
        storage = FakeStorage()
        seed_showcase_campaign(session, storage=storage)  # type: ignore[arg-type]

        version = session.get(AssetVersion, IMAGE_V2_ID)
        self.assertIsNotNone(version)
        metadata = version.generation_metadata  # type: ignore[union-attr]
        artifact_flow = metadata["artifact_flow"]
        artifact_key = artifact_flow["storage_key"]
        artifact_body, artifact_content_type = storage.objects[artifact_key]
        self.assertEqual(artifact_content_type, "image/png")
        self.assertEqual(
            hashlib.sha256(artifact_body).hexdigest(),
            artifact_flow["sha256"],
        )
        self.assertEqual(metadata["source"], "idempotent_demo_seed")
        self.assertTrue(metadata["manifest_verified"])
        self.assertEqual(len(metadata["input_assets"]), 3)
        self.assertEqual(
            metadata["based_on_version_id"],
            str(stable_demo_id("version/showcase-launch-image/v1")),
        )

        manifest_uri = metadata["manifest_uri"]
        manifest_key = manifest_uri.removeprefix("b2://demo-bucket/")
        manifest_body, manifest_content_type = storage.objects[manifest_key]
        self.assertEqual(manifest_content_type, "application/json")
        self.assertEqual(
            hashlib.sha256(manifest_body).hexdigest(),
            metadata["manifest_hash"],
        )
        manifest = json.loads(manifest_body)
        self.assertEqual(manifest["output"]["sha256"], artifact_flow["sha256"])

    def test_video_has_a_playable_artifact_and_completed_job_snapshot(self) -> None:
        session = FakeSession()
        storage = FakeStorage()
        seed_showcase_campaign(session, storage=storage)  # type: ignore[arg-type]

        version = session.get(AssetVersion, VIDEO_V1_ID)
        job = session.get(GenerationJob, VIDEO_JOB_ID)
        self.assertIsNotNone(version)
        self.assertIsNotNone(job)
        self.assertEqual(version.artifact_content_type, "video/webm")  # type: ignore[union-attr]
        self.assertGreater(version.artifact_size_bytes, 1000)  # type: ignore[union-attr]
        self.assertEqual(job.status, GenerationJobStatus.succeeded.value)  # type: ignore[union-attr]
        self.assertEqual(job.progress_percent, 100)  # type: ignore[union-attr]
        self.assertEqual(
            version.generation_metadata["job"]["id"],  # type: ignore[union-attr]
            str(VIDEO_JOB_ID),
        )
        video_body = storage.objects[version.artifact_storage_key][0]  # type: ignore[union-attr,index]
        self.assertEqual(video_body[:4], b"\x1aE\xdf\xa3")

    def test_existing_export_builder_packages_the_complete_showcase(self) -> None:
        session = FakeSession()
        storage = FakeStorage()
        seed_showcase_campaign(session, storage=storage)  # type: ignore[arg-type]
        campaign = wire_seeded_relationships(session)

        export_body = make_campaign_export_zip(
            campaign=campaign,
            storage=storage,  # type: ignore[arg-type]
            max_video_artifact_size_bytes=10 * 1024 * 1024,
        )

        with ZipFile(BytesIO(export_body)) as export_zip:
            names = set(export_zip.namelist())
            manifest = json.loads(export_zip.read("manifest.json"))

        self.assertIn("brand-assets/manifest.json", names)
        self.assertEqual(len(manifest["brand_assets"]), 3)
        self.assertEqual(len(manifest["assets"]), 2)
        versions = [
            version for asset in manifest["assets"] for version in asset["versions"]
        ]
        self.assertEqual(len(versions), 3)
        self.assertTrue(all(version["artifact_zip_path"] for version in versions))
        self.assertTrue(all(version["metadata_zip_path"] for version in versions))
        self.assertTrue(all(version["input_assets"] for version in versions))
        self.assertTrue(any(name.endswith(".webm") for name in names))


if __name__ == "__main__":
    unittest.main()
