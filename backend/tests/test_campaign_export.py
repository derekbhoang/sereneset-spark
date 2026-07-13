import hashlib
import json
import unittest
import uuid
from datetime import UTC, datetime
from io import BytesIO
from zipfile import ZipFile

from app.api.v1.routes.campaigns import make_campaign_export_zip
from app.models.brand_asset import BrandAsset, BrandAssetType, CampaignBrandAsset
from app.models.campaign import Campaign
from app.services.storage import B2StorageService


class StubStorage(B2StorageService):
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.downloads: list[str] = []

    def download_bytes(self, *, key: str) -> bytes:
        self.downloads.append(key)
        return self.objects[key]


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


if __name__ == "__main__":
    unittest.main()
