import hashlib
import json
import unittest
import uuid
from datetime import UTC, datetime
from io import BytesIO
from zipfile import ZIP_STORED, ZipFile

from app.api.v1.routes.campaigns import make_campaign_export_zip
from app.models.asset import Asset, AssetFormat, AssetVersion, ReviewStatus
from app.models.brand_asset import BrandAsset, BrandAssetType, CampaignBrandAsset
from app.models.campaign import Campaign
from app.services.storage import B2StorageService, StorageObjectTooLargeError


class StubStorage(B2StorageService):
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.downloads: list[str] = []
        self.streams: list[str] = []

    def download_bytes(self, *, key: str) -> bytes:
        self.downloads.append(key)
        return self.objects[key]

    def iter_download_chunks(
        self,
        *,
        key: str,
        chunk_size_bytes: int,
        max_size_bytes: int | None = None,
    ):
        self.streams.append(key)
        body = self.objects[key]
        if max_size_bytes is not None and len(body) > max_size_bytes:
            raise StorageObjectTooLargeError("configured size limit")

        for offset in range(0, len(body), chunk_size_bytes):
            yield body[offset : offset + chunk_size_bytes]


def make_campaign() -> Campaign:
    now = datetime.now(UTC)
    return Campaign(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        name="Export test",
        product="Product",
        audience="Audience",
        status="drafting",
        owner="Owner",
        goal="Goal",
        tone="Tone",
        brief="Brief",
        channels=[],
        brand_inputs=[],
        assets=[],
    )


def make_brand_asset(*, body: bytes, sha256: str | None = None) -> BrandAsset:
    now = datetime.now(UTC)
    brand_asset_id = uuid.uuid4()
    return BrandAsset(
        id=brand_asset_id,
        created_at=now,
        updated_at=now,
        name="Primary logo",
        asset_type=BrandAssetType.logo,
        description="Core campaign logo",
        usage_guidance="Keep the clear space intact",
        storage_key=f"brand-assets/{brand_asset_id}/original/logo.png",
        filename="logo.png",
        content_type="image/png",
        size_bytes=len(body),
        sha256=sha256 or hashlib.sha256(body).hexdigest(),
        tags=["core"],
        source_url=None,
        is_active=True,
    )


def attach(
    *,
    campaign: Campaign,
    brand_asset: BrandAsset,
    role: str,
) -> CampaignBrandAsset:
    return CampaignBrandAsset(
        id=uuid.uuid4(),
        campaign_id=campaign.id,
        brand_asset_id=brand_asset.id,
        role=role,
        created_at=datetime.now(UTC),
        brand_asset=brand_asset,
    )


def add_approved_video(
    *,
    campaign: Campaign,
    body: bytes,
) -> AssetVersion:
    now = datetime.now(UTC)
    asset_id = uuid.uuid4()
    version_id = uuid.uuid4()
    version = AssetVersion(
        id=version_id,
        asset_id=asset_id,
        version_number=1,
        label="Genblaze video 1",
        prompt="Show the product in motion",
        model="Veo3-Fast",
        provider="gmicloud",
        storage_key=f"campaigns/{campaign.id}/metadata.json",
        artifact_storage_key=f"campaigns/{campaign.id}/video.mp4",
        artifact_filename="launch-video.mp4",
        artifact_content_type="video/mp4",
        artifact_size_bytes=len(body),
        generation_metadata={"source": "backend_genblaze_video_worker"},
    )
    version.inputs = []
    asset = Asset(
        id=asset_id,
        campaign_id=campaign.id,
        created_at=now,
        updated_at=now,
        title="Launch video",
        format=AssetFormat.video_concept,
        channel="Paid social",
        status=ReviewStatus.approved,
        reviewer=None,
        tags=["video"],
        summary="Approved generated video",
    )
    asset.versions = [version]
    campaign.assets = [asset]
    campaign.brand_asset_links = []
    return version


class CampaignBrandAssetExportTests(unittest.TestCase):
    def test_exports_one_verified_file_for_multiple_role_attachments(self) -> None:
        body = b"verified-brand-asset"
        campaign = make_campaign()
        brand_asset = make_brand_asset(body=body)
        campaign.brand_asset_links = [
            attach(
                campaign=campaign,
                brand_asset=brand_asset,
                role="primary_logo",
            ),
            attach(
                campaign=campaign,
                brand_asset=brand_asset,
                role="watermark",
            ),
        ]
        storage = StubStorage({brand_asset.storage_key: body})

        archive = make_campaign_export_zip(
            campaign=campaign,
            storage=storage,
        )

        with ZipFile(BytesIO(archive)) as export_zip:
            manifest = json.loads(export_zip.read("manifest.json"))
            brand_manifest = json.loads(
                export_zip.read("brand-assets/manifest.json")
            )
            exported_files = [
                name for name in export_zip.namelist() if name.endswith("/logo.png")
            ]

        self.assertEqual(storage.downloads, [brand_asset.storage_key])
        self.assertEqual(len(exported_files), 1)
        self.assertEqual(manifest["brand_assets"], brand_manifest["brand_assets"])
        self.assertTrue(manifest["brand_assets"][0]["integrity_verified"])
        self.assertEqual(
            {
                attachment["role"]
                for attachment in manifest["brand_assets"][0]["attachments"]
            },
            {"primary_logo", "watermark"},
        )

    def test_checksum_mismatch_is_reported_without_breaking_export(self) -> None:
        stored_body = b"corrupted"
        campaign = make_campaign()
        brand_asset = make_brand_asset(
            body=stored_body,
            sha256=hashlib.sha256(b"expected").hexdigest(),
        )
        campaign.brand_asset_links = [
            attach(
                campaign=campaign,
                brand_asset=brand_asset,
                role="primary_logo",
            )
        ]
        storage = StubStorage({brand_asset.storage_key: stored_body})

        archive = make_campaign_export_zip(
            campaign=campaign,
            storage=storage,
        )

        with ZipFile(BytesIO(archive)) as export_zip:
            manifest = json.loads(export_zip.read("manifest.json"))
            exported_files = [
                name for name in export_zip.namelist() if name.endswith("/logo.png")
            ]

        brand_record = manifest["brand_assets"][0]
        self.assertEqual(exported_files, [])
        self.assertIsNone(brand_record["zip_path"])
        self.assertFalse(brand_record["integrity_verified"])
        self.assertIsNotNone(brand_record["export_error"])


class CampaignVideoExportTests(unittest.TestCase):
    def test_streams_video_into_an_uncompressed_zip_entry(self) -> None:
        video_body = b"video-data" * 220_000
        campaign = make_campaign()
        version = add_approved_video(campaign=campaign, body=video_body)
        storage = StubStorage(
            {
                version.storage_key: b'{"stored":true}',
                version.artifact_storage_key: video_body,
            }
        )

        archive = make_campaign_export_zip(
            campaign=campaign,
            storage=storage,
            max_video_artifact_size_bytes=len(video_body),
        )

        with ZipFile(BytesIO(archive)) as export_zip:
            manifest = json.loads(export_zip.read("manifest.json"))
            artifact_path = manifest["assets"][0]["versions"][0][
                "artifact_zip_path"
            ]
            artifact_info = export_zip.getinfo(artifact_path)
            exported_video = export_zip.read(artifact_path)

        self.assertEqual(exported_video, video_body)
        self.assertEqual(artifact_info.compress_type, ZIP_STORED)
        self.assertEqual(storage.streams, [version.artifact_storage_key])
        self.assertNotIn(version.artifact_storage_key, storage.downloads)


if __name__ == "__main__":
    unittest.main()
