import hashlib
import json
import unittest
import uuid
from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import patch
from zipfile import ZIP_STORED, ZipFile

from app.api.v1.routes.campaigns import make_campaign_export_zip
from app.models.asset import (
    Asset,
    AssetFormat,
    AssetVersion,
    AssetVersionInput,
    ReviewStatus,
)
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
        source_body = b"source-video"
        campaign = make_campaign()
        version = add_approved_video(campaign=campaign, body=video_body)
        source_asset_id = uuid.uuid4()
        source_version_id = uuid.uuid4()
        source_storage_key = f"campaigns/{campaign.id}/source.mp4"
        version.inputs = [
            AssetVersionInput(
                id=uuid.uuid4(),
                asset_version_id=version.id,
                role="source_creative",
                storage_key=source_storage_key,
                filename="source.mp4",
                content_type="video/mp4",
                media_kind="video",
                size_bytes=len(source_body),
                sha256=hashlib.sha256(source_body).hexdigest(),
                source="source_version_artifact",
                storage_ownership="source_asset_version",
                source_asset_id=source_asset_id,
                source_version_id=source_version_id,
                source_version_number=2,
                created_at=datetime.now(UTC),
            )
        ]
        content_validation = {
            "container": "mp4",
            "video_track_count": 1,
            "validated_at": datetime.now(UTC).isoformat(),
        }
        metadata_input = {
            "role": "source_creative",
            "storage_key": source_storage_key,
            "filename": "source.mp4",
            "content_type": "video/mp4",
            "media_kind": "video",
            "size_bytes": len(source_body),
            "sha256": hashlib.sha256(source_body).hexdigest(),
            "source": "source_version_artifact",
            "storage_ownership": "source_asset_version",
            "source_asset_id": str(source_asset_id),
            "source_version_id": str(source_version_id),
            "source_version_number": 2,
            "content_validation": content_validation,
        }
        version.generation_metadata = {
            "artifact_flow": {
                "storage_key": version.artifact_storage_key,
                "size_bytes": len(video_body),
                "sha256": hashlib.sha256(video_body).hexdigest(),
            },
            "input_assets": [metadata_input],
            "source_input_assets": [metadata_input],
            "provenance": {
                "schema_version": 2,
                "input_assets": [metadata_input],
                "request": {"source_input_assets": [metadata_input]},
            },
        }
        storage = StubStorage(
            {
                version.storage_key: b'{"stored":true}',
                version.artifact_storage_key: video_body,
                source_storage_key: source_body,
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
            input_record = manifest["assets"][0]["versions"][0][
                "input_assets"
            ][0]
            version_record = manifest["assets"][0]["versions"][0]
            input_path = input_record["zip_path"]
            input_info = export_zip.getinfo(input_path)
            exported_source = export_zip.read(input_path)

        self.assertEqual(exported_video, video_body)
        self.assertEqual(exported_source, source_body)
        self.assertEqual(artifact_info.compress_type, ZIP_STORED)
        self.assertEqual(input_info.compress_type, ZIP_STORED)
        self.assertEqual(
            storage.streams,
            [
                version.storage_key,
                version.artifact_storage_key,
                source_storage_key,
            ],
        )
        self.assertNotIn(version.artifact_storage_key, storage.downloads)
        self.assertNotIn(source_storage_key, storage.downloads)
        self.assertEqual(
            len(manifest["assets"][0]["versions"][0]["input_assets"]),
            1,
        )
        self.assertEqual(input_record["media_kind"], "video")
        self.assertEqual(input_record["source_asset_id"], str(source_asset_id))
        self.assertEqual(input_record["source_version_id"], str(source_version_id))
        self.assertEqual(input_record["source_version_number"], 2)
        self.assertEqual(input_record["content_validation"], content_validation)
        self.assertEqual(input_record["exported_size_bytes"], len(source_body))
        self.assertEqual(
            input_record["exported_sha256"],
            hashlib.sha256(source_body).hexdigest(),
        )
        self.assertTrue(input_record["size_verified"])
        self.assertTrue(input_record["sha256_verified"])
        self.assertTrue(input_record["integrity_verified"])
        self.assertEqual(
            version_record["artifact_exported_sha256"],
            hashlib.sha256(video_body).hexdigest(),
        )
        self.assertTrue(version_record["artifact_size_verified"])
        self.assertTrue(version_record["artifact_sha256_verified"])
        self.assertTrue(version_record["artifact_integrity_verified"])

    def test_omits_video_input_when_its_checksum_does_not_match(self) -> None:
        output_body = b"generated-video"
        source_body = b"corrupted-source-video"
        campaign = make_campaign()
        version = add_approved_video(campaign=campaign, body=output_body)
        source_storage_key = f"campaigns/{campaign.id}/source.mp4"
        version.inputs = [
            AssetVersionInput(
                id=uuid.uuid4(),
                asset_version_id=version.id,
                role="source_creative",
                storage_key=source_storage_key,
                filename="source.mp4",
                content_type="video/mp4",
                media_kind="video",
                size_bytes=len(source_body),
                sha256=hashlib.sha256(b"expected-source-video").hexdigest(),
                source="user_upload",
                storage_ownership="asset_version",
                created_at=datetime.now(UTC),
            )
        ]
        storage = StubStorage(
            {
                version.storage_key: b'{"stored":true}',
                version.artifact_storage_key: output_body,
                source_storage_key: source_body,
            }
        )

        archive = make_campaign_export_zip(
            campaign=campaign,
            storage=storage,
            max_video_artifact_size_bytes=len(output_body),
        )

        with ZipFile(BytesIO(archive)) as export_zip:
            manifest = json.loads(export_zip.read("manifest.json"))
            input_record = manifest["assets"][0]["versions"][0][
                "input_assets"
            ][0]
            exported_inputs = [
                name for name in export_zip.namelist() if name.startswith("inputs/")
            ]

        self.assertEqual(exported_inputs, [])
        self.assertIsNone(input_record["zip_path"])
        self.assertFalse(input_record["integrity_verified"])
        self.assertIsNotNone(input_record["export_error"])
        self.assertIn(source_storage_key, storage.streams)

    def test_rejects_oversized_video_input_before_downloading_it(self) -> None:
        output_body = b"generated-video"
        source_body = b"source-video-that-is-over-the-test-limit"
        campaign = make_campaign()
        version = add_approved_video(campaign=campaign, body=output_body)
        source_storage_key = f"campaigns/{campaign.id}/source.mp4"
        version.inputs = [
            AssetVersionInput(
                id=uuid.uuid4(),
                asset_version_id=version.id,
                role="source_creative",
                storage_key=source_storage_key,
                filename="source.mp4",
                content_type="video/mp4",
                media_kind="video",
                size_bytes=len(source_body),
                sha256=hashlib.sha256(source_body).hexdigest(),
                source="user_upload",
                storage_ownership="asset_version",
                created_at=datetime.now(UTC),
            )
        ]
        storage = StubStorage(
            {
                version.storage_key: b'{"stored":true}',
                version.artifact_storage_key: output_body,
                source_storage_key: source_body,
            }
        )

        archive = make_campaign_export_zip(
            campaign=campaign,
            storage=storage,
            max_video_artifact_size_bytes=len(output_body),
            max_video_input_size_bytes=len(source_body) - 1,
        )

        with ZipFile(BytesIO(archive)) as export_zip:
            manifest = json.loads(export_zip.read("manifest.json"))
            input_record = manifest["assets"][0]["versions"][0][
                "input_assets"
            ][0]

        self.assertIsNone(input_record["zip_path"])
        self.assertIsNotNone(input_record["export_error"])
        self.assertNotIn(source_storage_key, storage.streams)

    def test_omits_generated_artifact_when_its_checksum_does_not_match(
        self,
    ) -> None:
        output_body = b"corrupted-generated-video"
        campaign = make_campaign()
        version = add_approved_video(campaign=campaign, body=output_body)
        version.generation_metadata = {
            "artifact_flow": {
                "storage_key": version.artifact_storage_key,
                "sha256": hashlib.sha256(b"expected-generated-video").hexdigest(),
            }
        }
        storage = StubStorage(
            {
                version.storage_key: b'{"stored":true}',
                version.artifact_storage_key: output_body,
            }
        )

        archive = make_campaign_export_zip(
            campaign=campaign,
            storage=storage,
            max_video_artifact_size_bytes=len(output_body),
        )

        with ZipFile(BytesIO(archive)) as export_zip:
            manifest = json.loads(export_zip.read("manifest.json"))
            version_record = manifest["assets"][0]["versions"][0]
            exported_artifacts = [
                name
                for name in export_zip.namelist()
                if name.startswith("artifacts/")
            ]

        self.assertEqual(exported_artifacts, [])
        self.assertIsNone(version_record["artifact_zip_path"])
        self.assertIsNotNone(version_record["artifact_export_error"])

    def test_scrubs_temporary_urls_from_manifest_and_stored_sidecar(self) -> None:
        output_body = b"generated-video"
        source_body = b"source-video"
        campaign = make_campaign()
        version = add_approved_video(campaign=campaign, body=output_body)
        source_storage_key = f"campaigns/{campaign.id}/source.mp4"
        source_sha256 = hashlib.sha256(source_body).hexdigest()
        version.inputs = [
            AssetVersionInput(
                id=uuid.uuid4(),
                asset_version_id=version.id,
                role="source_creative",
                storage_key=source_storage_key,
                filename="source.mp4",
                content_type="video/mp4",
                media_kind="video",
                size_bytes=len(source_body),
                sha256=source_sha256,
                source="user_upload",
                storage_ownership="asset_version",
                created_at=datetime.now(UTC),
            )
        ]
        signed_url = (
            "https://s3.example.com/source.mp4?X-Amz-Signature=secret-token"
        )
        version.generation_metadata = {
            "manifest_uri": (
                "https://s3.example.com/manifest.json?token=secret-token"
            ),
            "provider_debug": {"signed_url": signed_url},
            "input_assets": [
                {
                    "role": "source_creative",
                    "storage_key": source_storage_key,
                    "url": signed_url,
                    "filename": "source.mp4",
                    "content_type": "video/mp4",
                    "media_kind": "video",
                    "size_bytes": len(source_body),
                    "sha256": source_sha256,
                }
            ],
        }
        storage = StubStorage(
            {
                version.storage_key: json.dumps(
                    version.generation_metadata
                ).encode("utf-8"),
                version.artifact_storage_key: output_body,
                source_storage_key: source_body,
            }
        )

        archive = make_campaign_export_zip(
            campaign=campaign,
            storage=storage,
            max_video_artifact_size_bytes=len(output_body),
        )

        with ZipFile(BytesIO(archive)) as export_zip:
            manifest_body = export_zip.read("manifest.json").decode("utf-8")
            manifest = json.loads(manifest_body)
            version_record = manifest["assets"][0]["versions"][0]
            sidecar_body = export_zip.read(
                version_record["metadata_zip_path"]
            ).decode("utf-8")
            sidecar = json.loads(sidecar_body)

        self.assertNotIn("secret-token", manifest_body)
        self.assertNotIn("secret-token", sidecar_body)
        self.assertEqual(
            version_record["generation_metadata"]["manifest_uri"],
            "https://s3.example.com/manifest.json",
        )
        self.assertNotIn(
            "url",
            version_record["generation_metadata"]["input_assets"][0],
        )
        self.assertEqual(
            sidecar["manifest_uri"],
            "https://s3.example.com/manifest.json",
        )
        self.assertNotIn("signed_url", sidecar["provider_debug"])

    def test_rejects_private_network_input_urls_without_requesting_them(
        self,
    ) -> None:
        output_body = b"generated-video"
        campaign = make_campaign()
        version = add_approved_video(campaign=campaign, body=output_body)
        version.generation_metadata = {
            "input_assets": [
                {
                    "role": "source_creative",
                    "url": "https://127.0.0.1/internal/source.mp4",
                    "filename": "source.mp4",
                    "content_type": "video/mp4",
                    "media_kind": "video",
                    "size_bytes": 1024,
                }
            ]
        }
        storage = StubStorage(
            {
                version.storage_key: b'{"stored":true}',
                version.artifact_storage_key: output_body,
            }
        )

        with patch(
            "app.api.v1.routes.campaigns.urlopen"
        ) as mocked_urlopen:
            archive = make_campaign_export_zip(
                campaign=campaign,
                storage=storage,
                max_video_artifact_size_bytes=len(output_body),
            )

        with ZipFile(BytesIO(archive)) as export_zip:
            manifest = json.loads(export_zip.read("manifest.json"))
            input_record = manifest["assets"][0]["versions"][0][
                "input_assets"
            ][0]

        mocked_urlopen.assert_not_called()
        self.assertIsNone(input_record["url"])
        self.assertIsNone(input_record["zip_path"])
        self.assertIsNotNone(input_record["export_error"])


if __name__ == "__main__":
    unittest.main()
