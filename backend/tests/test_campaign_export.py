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


def add_approved_video_refinement(
    *,
    campaign: Campaign,
    parent_body: bytes,
    refined_body: bytes,
) -> tuple[AssetVersion, AssetVersion, AssetVersionInput]:
    now = datetime.now(UTC)
    asset_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    refined_id = uuid.uuid4()
    parent_sha256 = hashlib.sha256(parent_body).hexdigest()
    refined_sha256 = hashlib.sha256(refined_body).hexdigest()
    parent_storage_key = (
        f"campaigns/{campaign.id}/assets/{asset_id}/versions/v1/artifact/parent.mp4"
    )
    refined_storage_key = (
        f"campaigns/{campaign.id}/assets/{asset_id}/versions/v2/artifact/refined.mp4"
    )
    parent = AssetVersion(
        id=parent_id,
        asset_id=asset_id,
        version_number=1,
        label="Genblaze video 1",
        prompt="Create the launch video",
        model="wan2.7-videoedit",
        provider="gmicloud",
        storage_key=(
            f"campaigns/{campaign.id}/assets/{asset_id}/versions/v1/metadata.json"
        ),
        artifact_storage_key=parent_storage_key,
        artifact_filename="parent.mp4",
        artifact_content_type="video/mp4",
        artifact_size_bytes=len(parent_body),
        generation_metadata={
            "operation": "video_generation",
            "artifact_flow": {
                "storage_key": parent_storage_key,
                "size_bytes": len(parent_body),
                "sha256": parent_sha256,
                "source_sha256": parent_sha256,
            },
        },
    )
    source_input = AssetVersionInput(
        id=uuid.uuid4(),
        asset_version_id=refined_id,
        role="source_creative",
        storage_key=parent_storage_key,
        filename="parent.mp4",
        content_type="video/mp4",
        media_kind="video",
        size_bytes=len(parent_body),
        sha256=parent_sha256,
        source="source_version_artifact",
        storage_ownership="source_asset_version",
        source_asset_id=asset_id,
        source_version_id=parent_id,
        source_version_number=1,
        created_at=now,
    )
    source_record = {
        "role": source_input.role,
        "storage_key": source_input.storage_key,
        "filename": source_input.filename,
        "content_type": source_input.content_type,
        "media_kind": source_input.media_kind,
        "size_bytes": source_input.size_bytes,
        "sha256": source_input.sha256,
        "source": source_input.source,
        "storage_ownership": source_input.storage_ownership,
        "source_asset_id": str(source_input.source_asset_id),
        "source_version_id": str(source_input.source_version_id),
        "source_version_number": source_input.source_version_number,
    }
    source_resolution = {
        "origin": "asset_version",
        "source_version_id": str(parent_id),
        "source_brand_asset_id": None,
    }
    refined = AssetVersion(
        id=refined_id,
        asset_id=asset_id,
        version_number=2,
        label="Video refinement 2",
        prompt="Make the background move more slowly",
        model="wan2.7-videoedit",
        provider="gmicloud",
        storage_key=(
            f"campaigns/{campaign.id}/assets/{asset_id}/versions/v2/metadata.json"
        ),
        artifact_storage_key=refined_storage_key,
        artifact_filename="refined.mp4",
        artifact_content_type="video/mp4",
        artifact_size_bytes=len(refined_body),
        generation_metadata={
            "operation": "video_refinement",
            "based_on_version_id": str(parent_id),
            "source_resolution": source_resolution,
            "input_assets": [source_record],
            "artifact_flow": {
                "storage_key": refined_storage_key,
                "size_bytes": len(refined_body),
                "sha256": refined_sha256,
                "source_sha256": refined_sha256,
            },
            "provenance": {
                "operation": "video_refinement",
                "based_on_version_id": str(parent_id),
                "source_resolution": source_resolution,
                "input_assets": [source_record],
                "request": {
                    "operation": "video_refinement",
                    "based_on_version_id": str(parent_id),
                    "source_resolution": source_resolution,
                },
            },
            "request": {
                "operation": "video_refinement",
                "based_on_version_id": str(parent_id),
                "source_resolution": source_resolution,
            },
        },
    )
    parent.inputs = []
    refined.inputs = [source_input]
    asset = Asset(
        id=asset_id,
        campaign_id=campaign.id,
        created_at=now,
        updated_at=now,
        title="Refined launch video",
        format=AssetFormat.video_concept,
        channel="Paid social",
        status=ReviewStatus.approved,
        reviewer=None,
        tags=["video", "refinement"],
        summary="Approved refined video",
    )
    asset.versions = [parent, refined]
    campaign.assets = [asset]
    campaign.brand_asset_links = []
    return parent, refined, source_input


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
    def test_verifies_and_exports_video_refinement_lineage(self) -> None:
        parent_body = b"parent-video"
        refined_body = b"refined-video"
        campaign = make_campaign()
        parent, refined, source_input = add_approved_video_refinement(
            campaign=campaign,
            parent_body=parent_body,
            refined_body=refined_body,
        )
        storage = StubStorage(
            {
                parent.storage_key: b'{"version":1}',
                parent.artifact_storage_key: parent_body,
                refined.storage_key: b'{"version":2}',
                refined.artifact_storage_key: refined_body,
            }
        )

        archive = make_campaign_export_zip(
            campaign=campaign,
            storage=storage,
            max_video_artifact_size_bytes=max(
                len(parent_body),
                len(refined_body),
            ),
        )

        with ZipFile(BytesIO(archive)) as export_zip:
            manifest = json.loads(export_zip.read("manifest.json"))
            parent_record, refined_record = manifest["assets"][0]["versions"]
            lineage = refined_record["export_lineage"]
            exported_source = export_zip.read(lineage["source_input_zip_path"])

        self.assertIsNone(parent_record["export_lineage"])
        self.assertEqual(lineage["operation"], "video_refinement")
        self.assertEqual(lineage["based_on_version_id"], str(parent.id))
        self.assertEqual(lineage["source_input_id"], str(source_input.id))
        self.assertEqual(lineage["source_asset_id"], str(parent.asset_id))
        self.assertEqual(lineage["source_version_id"], str(parent.id))
        self.assertEqual(lineage["source_version_number"], 1)
        self.assertEqual(
            lineage["parent_metadata_zip_path"],
            parent_record["metadata_zip_path"],
        )
        self.assertEqual(
            lineage["parent_artifact_zip_path"],
            parent_record["artifact_zip_path"],
        )
        self.assertEqual(
            lineage["source_input_zip_path"],
            refined_record["input_assets"][0]["zip_path"],
        )
        self.assertTrue(lineage["snapshot_verified"])
        self.assertTrue(lineage["parent_artifact_integrity_verified"])
        self.assertTrue(lineage["source_input_integrity_verified"])
        self.assertTrue(lineage["integrity_verified"])
        self.assertIsNone(lineage["export_error"])
        self.assertEqual(exported_source, parent_body)

    def test_does_not_export_a_tampered_refinement_source(self) -> None:
        parent_body = b"parent-video"
        refined_body = b"refined-video"
        untrusted_body = b"untrusted-video"
        campaign = make_campaign()
        parent, refined, source_input = add_approved_video_refinement(
            campaign=campaign,
            parent_body=parent_body,
            refined_body=refined_body,
        )
        untrusted_storage_key = f"campaigns/{campaign.id}/untrusted.mp4"
        source_input.storage_key = untrusted_storage_key
        storage = StubStorage(
            {
                parent.storage_key: b'{"version":1}',
                parent.artifact_storage_key: parent_body,
                refined.storage_key: b'{"version":2}',
                refined.artifact_storage_key: refined_body,
                untrusted_storage_key: untrusted_body,
            }
        )

        archive = make_campaign_export_zip(
            campaign=campaign,
            storage=storage,
            max_video_artifact_size_bytes=max(
                len(parent_body),
                len(refined_body),
            ),
        )

        with ZipFile(BytesIO(archive)) as export_zip:
            manifest = json.loads(export_zip.read("manifest.json"))
            refined_record = manifest["assets"][0]["versions"][1]
            lineage = refined_record["export_lineage"]
            input_paths = [
                name
                for name in export_zip.namelist()
                if name.startswith("inputs/")
            ]

        self.assertEqual(input_paths, [])
        self.assertNotIn(untrusted_storage_key, storage.streams)
        self.assertFalse(lineage["snapshot_verified"])
        self.assertTrue(lineage["parent_artifact_integrity_verified"])
        self.assertFalse(lineage["source_input_integrity_verified"])
        self.assertFalse(lineage["integrity_verified"])
        self.assertIn("storage key", lineage["export_error"])
        self.assertTrue(refined_record["artifact_integrity_verified"])
        self.assertTrue(refined_record["input_assets"])
        self.assertTrue(
            all(
                input_record["zip_path"] is None
                and "lineage" in input_record["export_error"]
                for input_record in refined_record["input_assets"]
            )
        )

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
