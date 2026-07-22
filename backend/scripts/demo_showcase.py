from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.asset import (
    Asset,
    AssetFormat,
    AssetVersion,
    AssetVersionInput,
    ReviewStatus,
)
from app.models.brand_asset import (
    BrandAsset,
    BrandAssetType,
    CampaignBrandAsset,
)
from app.models.campaign import Campaign
from app.models.generation_job import (
    GenerationJob,
    GenerationJobKind,
    GenerationJobStatus,
)
from app.services.input_provenance import infer_input_media_kind
from app.services.storage import (
    StoredObject,
    build_asset_version_artifact_storage_key,
    build_asset_version_storage_key,
    build_brand_asset_storage_key,
    normalize_storage_key,
)


SHOWCASE_CAMPAIGN_NAME = "SereneSet Essentials Launch (Showcase)"
SHOWCASE_SOURCE = "idempotent_demo_seed"
SHOWCASE_RECORDED_AT = datetime(2026, 7, 16, 9, 30, tzinfo=UTC)
FIXTURE_DIR = Path(__file__).resolve().parent / "demo_assets"


class SeedStorage(Protocol):
    bucket_name: str

    def upload_bytes(
        self,
        *,
        key: str,
        body: bytes,
        content_type: str,
        metadata: dict[str, Any] | None = None,
        cache_control: str | None = None,
    ) -> StoredObject: ...

    def upload_json(
        self,
        *,
        key: str,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> StoredObject: ...


@dataclass(frozen=True)
class BrandFixture:
    token: str
    name: str
    asset_type: BrandAssetType
    role: str
    fixture_filename: str
    content_type: str
    description: str
    usage_guidance: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class ArtifactFixture:
    token: str
    filename: str
    content_type: str
    prompt: str
    model: str
    recorded_at: datetime
    generation_parameters: dict[str, object]


BRAND_FIXTURES = (
    BrandFixture(
        token="wordmark",
        name="SereneSet Primary Wordmark",
        asset_type=BrandAssetType.logo,
        role="brand_reference",
        fixture_filename="sereneset-wordmark.svg",
        content_type="image/svg+xml",
        description="Primary horizontal wordmark with launch palette accents.",
        usage_guidance=(
            "Keep clear space around the mark and use only on quiet, "
            "high-contrast backgrounds."
        ),
        tags=("logo", "approved", "launch-system"),
    ),
    BrandFixture(
        token="product-stack",
        name="SereneSet Essentials Product Stack",
        asset_type=BrandAssetType.product_image,
        role="product",
        fixture_filename="sereneset-product-stack.png",
        content_type="image/png",
        description="Transparent product stack used as the hero source creative.",
        usage_guidance=(
            "Preserve the complete silhouette, violet edge treatment, and "
            "layer spacing. Do not crop through the product."
        ),
        tags=("product", "approved", "transparent"),
    ),
    BrandFixture(
        token="guidelines",
        name="SereneSet Launch Guidelines",
        asset_type=BrandAssetType.guideline,
        role="guideline",
        fixture_filename="brand-guidelines.md",
        content_type="text/markdown",
        description="Voice, visual direction, required copy, and channel notes.",
        usage_guidance="Treat required copy and prohibited claims as binding.",
        tags=("guidelines", "voice", "launch-system"),
    ),
)


IMAGE_V1 = ArtifactFixture(
    token="image-v1",
    filename="launch-concept-v1.png",
    content_type="image/png",
    prompt=(
        "Create a product-first paid social launch frame for the SereneSet "
        "Essentials Kit. Keep the product central, use charcoal, violet, mint, "
        "and coral, and lead with 'Make room for a calmer reset.'"
    ),
    model="sereneset-showcase-image-v1",
    recorded_at=SHOWCASE_RECORDED_AT,
    generation_parameters={
        "width": 1200,
        "height": 675,
        "channel": "Paid social",
        "format": "image",
    },
)


IMAGE_V2 = ArtifactFixture(
    token="image-v2",
    filename="launch-concept-v2.png",
    content_type="image/png",
    prompt=(
        "Refine the launch frame around 'One calm decision at a time.' Preserve "
        "the product geometry, increase headline contrast, and make the "
        "Discover the kit action unmistakable."
    ),
    model="sereneset-showcase-image-v2",
    recorded_at=SHOWCASE_RECORDED_AT.replace(minute=42),
    generation_parameters={
        "width": 1200,
        "height": 675,
        "channel": "Paid social",
        "format": "image",
        "refinement": True,
    },
)


VIDEO_V1 = ArtifactFixture(
    token="video-v1",
    filename="launch-motion.webm",
    content_type="video/webm",
    prompt=(
        "Animate the approved SereneSet launch frame into a restrained "
        "four-second 16:9 motion asset. Reveal the product in the first two "
        "seconds, preserve the approved palette, and end on Discover the kit."
    ),
    model="sereneset-showcase-video-v1",
    recorded_at=SHOWCASE_RECORDED_AT.replace(hour=10, minute=4),
    generation_parameters={
        "duration": 4,
        "aspect_ratio": "16:9",
        "resolution": "640x360",
        "fps": 24,
        "input_mode": "image_to_video",
    },
)


ModelT = TypeVar("ModelT")


def stable_demo_id(token: str) -> uuid.UUID:
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"https://sereneset-spark.local/demo/{token}",
    )


SHOWCASE_CAMPAIGN_ID = stable_demo_id("campaign/showcase-launch")
IMAGE_ASSET_ID = stable_demo_id("asset/showcase-launch-image")
IMAGE_V1_ID = stable_demo_id("version/showcase-launch-image/v1")
IMAGE_V2_ID = stable_demo_id("version/showcase-launch-image/v2")
VIDEO_ASSET_ID = stable_demo_id("asset/showcase-launch-video")
VIDEO_V1_ID = stable_demo_id("version/showcase-launch-video/v1")
VIDEO_JOB_ID = stable_demo_id("job/showcase-launch-video/v1")


def fixture_bytes(filename: str) -> bytes:
    path = FIXTURE_DIR / filename
    body = path.read_bytes()
    if not body:
        raise ValueError(f"Demo fixture is empty: {path}")
    return body


def sha256_hex(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_json_bytes(data: dict[str, object]) -> bytes:
    return json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def set_fields(model: object, values: dict[str, Any]) -> None:
    for key, value in values.items():
        setattr(model, key, value)


def upsert_by_id(
    db: Session,
    model_type: type[ModelT],
    object_id: uuid.UUID,
    values: dict[str, Any],
) -> tuple[ModelT, bool]:
    model = db.get(model_type, object_id)
    created = model is None
    if model is None:
        model = model_type(id=object_id, **values)
        db.add(model)
    else:
        set_fields(model, values)
    db.flush()
    return model, created


def increment_count(
    counts: dict[str, int],
    group: str,
    created: bool,
) -> None:
    counts[f"{group}_{'created' if created else 'updated'}"] += 1


def upload_bytes(
    storage: SeedStorage | None,
    *,
    key: str,
    body: bytes,
    content_type: str,
    metadata: dict[str, Any],
    counts: dict[str, int],
) -> None:
    if storage is None:
        return
    storage.upload_bytes(
        key=key,
        body=body,
        content_type=content_type,
        metadata=metadata,
    )
    counts["storage_objects_uploaded"] += 1


def brand_asset_input_record(
    *,
    brand_asset: BrandAsset,
    link: CampaignBrandAsset,
) -> dict[str, object]:
    return {
        "role": link.role,
        "storage_key": brand_asset.storage_key,
        "filename": brand_asset.filename,
        "content_type": brand_asset.content_type,
        "media_kind": infer_input_media_kind(brand_asset.content_type).value,
        "size_bytes": brand_asset.size_bytes,
        "sha256": brand_asset.sha256,
        "source": "campaign_brand_asset",
        "storage_ownership": "brand_asset",
        "brand_asset_id": str(brand_asset.id),
        "campaign_brand_asset_id": str(link.id),
        "brand_asset_type": brand_asset.asset_type.value,
        "brand_asset_name": brand_asset.name,
        "usage_guidance": brand_asset.usage_guidance,
    }


def source_version_input_record(
    *,
    role: str,
    source_version: AssetVersion,
    sha256: str,
) -> dict[str, object]:
    content_type = source_version.artifact_content_type or "application/octet-stream"
    return {
        "role": role,
        "storage_key": source_version.artifact_storage_key,
        "filename": source_version.artifact_filename,
        "content_type": content_type,
        "media_kind": infer_input_media_kind(content_type).value,
        "size_bytes": source_version.artifact_size_bytes,
        "sha256": sha256,
        "source": "source_version_artifact",
        "storage_ownership": "source_asset_version",
        "source_asset_id": str(source_version.asset_id),
        "source_version_id": str(source_version.id),
        "source_version_number": source_version.version_number,
    }


def input_model_values(record: dict[str, object]) -> dict[str, object]:
    return {
        key: record.get(key)
        for key in (
            "asset_version_id",
            "role",
            "storage_key",
            "filename",
            "content_type",
            "media_kind",
            "size_bytes",
            "sha256",
            "source",
            "storage_ownership",
            "source_asset_id",
            "source_version_id",
            "source_version_number",
            "brand_asset_id",
            "campaign_brand_asset_id",
            "brand_asset_type",
            "brand_asset_name",
            "usage_guidance",
        )
    }


def upsert_version_input(
    db: Session,
    *,
    version: AssetVersion,
    token: str,
    record: dict[str, object],
    counts: dict[str, int],
) -> AssetVersionInput:
    model_record = {
        **record,
        "asset_version_id": version.id,
        "brand_asset_id": (
            uuid.UUID(str(record["brand_asset_id"]))
            if record.get("brand_asset_id")
            else None
        ),
        "campaign_brand_asset_id": (
            uuid.UUID(str(record["campaign_brand_asset_id"]))
            if record.get("campaign_brand_asset_id")
            else None
        ),
        "source_asset_id": (
            uuid.UUID(str(record["source_asset_id"]))
            if record.get("source_asset_id")
            else None
        ),
        "source_version_id": (
            uuid.UUID(str(record["source_version_id"]))
            if record.get("source_version_id")
            else None
        ),
    }
    version_input, created = upsert_by_id(
        db,
        AssetVersionInput,
        stable_demo_id(f"input/{token}"),
        input_model_values(model_record),
    )
    increment_count(counts, "inputs", created)
    return version_input


def manifest_payload(
    *,
    version: AssetVersion,
    fixture: ArtifactFixture,
    artifact_storage_key: str,
    artifact_sha256: str,
    artifact_size_bytes: int,
    input_assets: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": str(stable_demo_id(f"run/{fixture.token}")),
        "status": "succeeded",
        "source": SHOWCASE_SOURCE,
        "recorded_at": fixture.recorded_at.isoformat(),
        "provider": "demo-seed",
        "model": fixture.model,
        "prompt_sha256": sha256_hex(fixture.prompt.encode("utf-8")),
        "input_assets": input_assets,
        "output": {
            "storage_key": artifact_storage_key,
            "filename": fixture.filename,
            "content_type": fixture.content_type,
            "size_bytes": artifact_size_bytes,
            "sha256": artifact_sha256,
        },
        "asset_version_id": str(version.id),
    }


def build_generation_metadata(
    *,
    version: AssetVersion,
    fixture: ArtifactFixture,
    artifact_storage_key: str,
    artifact_sha256: str,
    artifact_size_bytes: int,
    input_assets: list[dict[str, object]],
    manifest_uri: str | None,
    manifest_hash: str,
    manifest_verified: bool,
    based_on_version_id: uuid.UUID | None = None,
    job_record: dict[str, object] | None = None,
) -> dict[str, Any]:
    artifact = {
        "url": None,
        "storage_key": artifact_storage_key,
        "sha256": artifact_sha256,
        "content_type": fixture.content_type,
        "size_bytes": artifact_size_bytes,
        "filename": fixture.filename,
    }
    artifact_flow = {
        "storage_key": artifact_storage_key,
        "filename": fixture.filename,
        "content_type": fixture.content_type,
        "size_bytes": artifact_size_bytes,
        "source": SHOWCASE_SOURCE,
        "storage_strategy": "deterministic_put_object",
        "source_storage_key": artifact_storage_key,
        "sha256": artifact_sha256,
        "source_sha256": artifact_sha256,
    }
    provenance: dict[str, Any] = {
        "schema_version": 1,
        "provider": "demo-seed",
        "model": fixture.model,
        "prompt": fixture.prompt,
        "source": SHOWCASE_SOURCE,
        "based_on_version_id": (
            str(based_on_version_id) if based_on_version_id else None
        ),
        "generation_parameters": fixture.generation_parameters,
        "manifest_uri": manifest_uri,
        "manifest_hash": manifest_hash,
        "manifest_verified": manifest_verified,
        "input_assets": input_assets,
        "assets": [artifact],
        "artifact_flow": artifact_flow,
        "recorded_at": fixture.recorded_at.isoformat(),
        "seed_notice": "Deterministic demo fixture; not a live provider run.",
    }
    if job_record is not None:
        provenance["job"] = job_record

    metadata: dict[str, Any] = {
        "provenance_schema_version": 1,
        "provider": "demo-seed",
        "model": fixture.model,
        "prompt": fixture.prompt,
        "source": SHOWCASE_SOURCE,
        "based_on_version_id": (
            str(based_on_version_id) if based_on_version_id else None
        ),
        "generation_parameters": fixture.generation_parameters,
        "manifest_uri": manifest_uri,
        "manifest_hash": manifest_hash,
        "manifest_verified": manifest_verified,
        "input_assets": input_assets,
        "assets": [artifact],
        "artifact_flow": artifact_flow,
        "provenance": provenance,
        "seed": {
            "fixture": fixture.filename,
            "deterministic": True,
            "notice": "Demo fixture; not a live provider run.",
        },
    }
    if job_record is not None:
        metadata["job"] = job_record
    return metadata


def build_version_sidecar(
    *,
    campaign: Campaign,
    asset: Asset,
    version: AssetVersion,
    input_assets: list[dict[str, object]],
    stored_at: datetime,
) -> dict[str, object]:
    return {
        "campaign": {
            "id": str(campaign.id),
            "name": campaign.name,
            "product": campaign.product,
            "audience": campaign.audience,
            "status": campaign.status,
            "channels": campaign.channels,
            "brand_inputs": campaign.brand_inputs,
        },
        "asset": {
            "id": str(asset.id),
            "title": asset.title,
            "format": asset.format.value,
            "channel": asset.channel,
            "status": asset.status.value,
            "reviewer": asset.reviewer,
            "tags": asset.tags,
            "summary": asset.summary,
        },
        "version": {
            "id": str(version.id),
            "version_number": version.version_number,
            "label": version.label,
            "prompt": version.prompt,
            "model": version.model,
            "provider": version.provider,
            "storage_key": version.storage_key,
            "artifact_storage_key": version.artifact_storage_key,
            "artifact_filename": version.artifact_filename,
            "artifact_content_type": version.artifact_content_type,
            "artifact_size_bytes": version.artifact_size_bytes,
            "input_assets": input_assets,
            "generation_metadata": version.generation_metadata,
        },
        "stored_at": stored_at.isoformat(),
    }


def seed_brand_assets(
    db: Session,
    *,
    campaign: Campaign,
    storage: SeedStorage | None,
    counts: dict[str, int],
) -> dict[str, tuple[BrandAsset, CampaignBrandAsset, dict[str, object]]]:
    seeded: dict[
        str,
        tuple[BrandAsset, CampaignBrandAsset, dict[str, object]],
    ] = {}
    for fixture in BRAND_FIXTURES:
        brand_asset_id = stable_demo_id(f"brand-asset/{fixture.token}")
        body = fixture_bytes(fixture.fixture_filename)
        storage_key = build_brand_asset_storage_key(
            brand_asset_id=brand_asset_id,
            filename=fixture.fixture_filename,
        )
        digest = sha256_hex(body)
        upload_bytes(
            storage,
            key=storage_key,
            body=body,
            content_type=fixture.content_type,
            metadata={
                "brand_asset_id": str(brand_asset_id),
                "asset_type": fixture.asset_type.value,
                "content_kind": "demo-brand-asset",
            },
            counts=counts,
        )
        brand_asset, created = upsert_by_id(
            db,
            BrandAsset,
            brand_asset_id,
            {
                "name": fixture.name,
                "asset_type": fixture.asset_type,
                "description": fixture.description,
                "usage_guidance": fixture.usage_guidance,
                "storage_key": storage_key,
                "filename": fixture.fixture_filename,
                "content_type": fixture.content_type,
                "size_bytes": len(body),
                "sha256": digest,
                "tags": list(fixture.tags),
                "source_url": None,
                "is_active": True,
            },
        )
        increment_count(counts, "brand_assets", created)
        link, link_created = upsert_by_id(
            db,
            CampaignBrandAsset,
            stable_demo_id(f"campaign-brand-asset/{fixture.token}"),
            {
                "campaign_id": campaign.id,
                "brand_asset_id": brand_asset.id,
                "role": fixture.role,
            },
        )
        increment_count(counts, "brand_links", link_created)
        seeded[fixture.token] = (
            brand_asset,
            link,
            brand_asset_input_record(brand_asset=brand_asset, link=link),
        )
    return seeded


def seed_artifact_version(
    db: Session,
    *,
    campaign: Campaign,
    asset: Asset,
    version_id: uuid.UUID,
    version_number: int,
    label: str,
    fixture: ArtifactFixture,
    input_records: list[tuple[str, dict[str, object]]],
    storage: SeedStorage | None,
    counts: dict[str, int],
    based_on_version_id: uuid.UUID | None = None,
    job_record: dict[str, object] | None = None,
) -> tuple[AssetVersion, str]:
    body = fixture_bytes(fixture.filename)
    artifact_sha256 = sha256_hex(body)
    artifact_storage_key = build_asset_version_artifact_storage_key(
        campaign_id=campaign.id,
        asset_id=asset.id,
        version_number=version_number,
        filename=fixture.filename,
    )
    sidecar_storage_key = build_asset_version_storage_key(
        campaign_id=campaign.id,
        asset_id=asset.id,
        version_number=version_number,
    )
    upload_bytes(
        storage,
        key=artifact_storage_key,
        body=body,
        content_type=fixture.content_type,
        metadata={
            "campaign_id": str(campaign.id),
            "asset_id": str(asset.id),
            "version_number": version_number,
            "content_kind": "demo-generated-artifact",
            "sha256": artifact_sha256,
        },
        counts=counts,
    )
    version, created = upsert_by_id(
        db,
        AssetVersion,
        version_id,
        {
            "asset_id": asset.id,
            "version_number": version_number,
            "label": label,
            "prompt": fixture.prompt,
            "model": fixture.model,
            "provider": "demo-seed",
            "storage_key": sidecar_storage_key,
            "artifact_storage_key": artifact_storage_key,
            "artifact_filename": fixture.filename,
            "artifact_content_type": fixture.content_type,
            "artifact_size_bytes": len(body),
            "generation_metadata": {},
        },
    )
    increment_count(counts, "versions", created)

    provenance_inputs: list[dict[str, object]] = []
    for input_token, input_record in input_records:
        upsert_version_input(
            db,
            version=version,
            token=f"{fixture.token}/{input_token}",
            record=input_record,
            counts=counts,
        )
        provenance_inputs.append(input_record)

    manifest = manifest_payload(
        version=version,
        fixture=fixture,
        artifact_storage_key=artifact_storage_key,
        artifact_sha256=artifact_sha256,
        artifact_size_bytes=len(body),
        input_assets=provenance_inputs,
    )
    manifest_body = canonical_json_bytes(manifest)
    manifest_hash = sha256_hex(manifest_body)
    manifest_storage_key = normalize_storage_key(
        "/".join(
            [
                "campaigns",
                str(campaign.id),
                "demo-runs",
                str(stable_demo_id(f"run/{fixture.token}")),
                "manifest.json",
            ]
        )
    )
    upload_bytes(
        storage,
        key=manifest_storage_key,
        body=manifest_body,
        content_type="application/json",
        metadata={
            "asset_version_id": str(version.id),
            "content_kind": "demo-generation-manifest",
            "sha256": manifest_hash,
        },
        counts=counts,
    )
    manifest_uri = (
        f"b2://{storage.bucket_name}/{manifest_storage_key}"
        if storage is not None
        else None
    )
    version.generation_metadata = build_generation_metadata(
        version=version,
        fixture=fixture,
        artifact_storage_key=artifact_storage_key,
        artifact_sha256=artifact_sha256,
        artifact_size_bytes=len(body),
        input_assets=provenance_inputs,
        manifest_uri=manifest_uri,
        manifest_hash=manifest_hash,
        manifest_verified=storage is not None,
        based_on_version_id=based_on_version_id,
        job_record=job_record,
    )
    db.flush()

    if storage is not None:
        storage.upload_json(
            key=sidecar_storage_key,
            data=build_version_sidecar(
                campaign=campaign,
                asset=asset,
                version=version,
                input_assets=provenance_inputs,
                stored_at=fixture.recorded_at,
            ),
            metadata={
                "campaign_id": str(campaign.id),
                "asset_id": str(asset.id),
                "version_number": version.version_number,
                "content_kind": "asset-version-sidecar",
            },
        )
        counts["storage_objects_uploaded"] += 1
    return version, artifact_sha256


def seed_showcase_campaign(
    db: Session,
    *,
    storage: SeedStorage | None,
) -> dict[str, int]:
    counts = {
        "campaigns_created": 0,
        "campaigns_updated": 0,
        "assets_created": 0,
        "assets_updated": 0,
        "versions_created": 0,
        "versions_updated": 0,
        "brand_assets_created": 0,
        "brand_assets_updated": 0,
        "brand_links_created": 0,
        "brand_links_updated": 0,
        "inputs_created": 0,
        "inputs_updated": 0,
        "generation_jobs_created": 0,
        "generation_jobs_updated": 0,
        "storage_objects_uploaded": 0,
    }
    campaign, created = upsert_by_id(
        db,
        Campaign,
        SHOWCASE_CAMPAIGN_ID,
        {
            "name": SHOWCASE_CAMPAIGN_NAME,
            "product": "SereneSet Essentials Kit",
            "audience": "Design-aware wellness shoppers, 28-44",
            "status": "ready",
            "due_date": date(2026, 8, 14),
            "owner": "Mira Chen",
            "goal": (
                "Ship a review-ready paid launch system with durable media, "
                "traceable inputs, and a complete handoff pack."
            ),
            "tone": "Calm, precise, useful",
            "brief": (
                "Launch the SereneSet Essentials Kit as one considered daily "
                "reset. Keep the product central, avoid clinical claims, and "
                "carry one visual system from still image into motion."
            ),
            "channels": ["Paid social", "Email", "Display"],
            "brand_inputs": [
                "Primary wordmark",
                "Essentials product stack",
                "Launch voice and visual guidelines",
            ],
        },
    )
    increment_count(counts, "campaigns", created)
    brand_assets = seed_brand_assets(
        db,
        campaign=campaign,
        storage=storage,
        counts=counts,
    )

    image_asset, created = upsert_by_id(
        db,
        Asset,
        IMAGE_ASSET_ID,
        {
            "campaign_id": campaign.id,
            "title": "Paid social launch hero",
            "format": AssetFormat.image,
            "channel": "Paid social",
            "status": ReviewStatus.approved,
            "reviewer": "Mira Chen",
            "tags": ["approved", "launch", "image", "demo"],
            "summary": (
                "A product-first launch composition refined into an approved "
                "high-contrast campaign hero."
            ),
        },
    )
    increment_count(counts, "assets", created)
    product_input = brand_assets["product-stack"][2]
    wordmark_input = brand_assets["wordmark"][2]
    image_v1, image_v1_sha256 = seed_artifact_version(
        db,
        campaign=campaign,
        asset=image_asset,
        version_id=IMAGE_V1_ID,
        version_number=1,
        label="Product-first launch concept",
        fixture=IMAGE_V1,
        input_records=[
            ("product", product_input),
            ("wordmark", wordmark_input),
        ],
        storage=storage,
        counts=counts,
    )
    image_v1_source = source_version_input_record(
        role="source_creative",
        source_version=image_v1,
        sha256=image_v1_sha256,
    )
    image_v2, image_v2_sha256 = seed_artifact_version(
        db,
        campaign=campaign,
        asset=image_asset,
        version_id=IMAGE_V2_ID,
        version_number=2,
        label="Approved launch composition",
        fixture=IMAGE_V2,
        input_records=[
            ("source-creative", image_v1_source),
            ("product", product_input),
            ("wordmark", wordmark_input),
        ],
        storage=storage,
        counts=counts,
        based_on_version_id=image_v1.id,
    )

    video_asset, created = upsert_by_id(
        db,
        Asset,
        VIDEO_ASSET_ID,
        {
            "campaign_id": campaign.id,
            "title": "Four-second launch motion",
            "format": AssetFormat.video_concept,
            "channel": "Paid social",
            "status": ReviewStatus.approved,
            "reviewer": "Mira Chen",
            "tags": ["approved", "launch", "video", "demo"],
            "summary": (
                "A restrained 16:9 motion cut derived from the approved image "
                "and ready for export with its input and job provenance."
            ),
        },
    )
    increment_count(counts, "assets", created)
    video_job_record = {
        "id": str(VIDEO_JOB_ID),
        "kind": GenerationJobKind.video.value,
        "status": GenerationJobStatus.succeeded.value,
        "progress_percent": 100,
        "provider_job_id": f"demo-{VIDEO_JOB_ID}",
        "attempt_count": 1,
        "error_message": None,
        "started_at": VIDEO_V1.recorded_at.replace(minute=0).isoformat(),
        "completed_at": VIDEO_V1.recorded_at.isoformat(),
    }
    video_source = source_version_input_record(
        role="source_creative",
        source_version=image_v2,
        sha256=image_v2_sha256,
    )
    video_version, _video_sha256 = seed_artifact_version(
        db,
        campaign=campaign,
        asset=video_asset,
        version_id=VIDEO_V1_ID,
        version_number=1,
        label="Approved four-second motion cut",
        fixture=VIDEO_V1,
        input_records=[
            ("source-creative", video_source),
            ("product", product_input),
            ("wordmark", wordmark_input),
        ],
        storage=storage,
        counts=counts,
        based_on_version_id=image_v2.id,
        job_record=video_job_record,
    )
    job, created = upsert_by_id(
        db,
        GenerationJob,
        VIDEO_JOB_ID,
        {
            "asset_version_id": video_version.id,
            "kind": GenerationJobKind.video.value,
            "status": GenerationJobStatus.succeeded.value,
            "provider": "demo-seed",
            "model": VIDEO_V1.model,
            "prompt": VIDEO_V1.prompt,
            "parameters": {
                **VIDEO_V1.generation_parameters,
                "source_version_id": str(image_v2.id),
                "source_input_assets": [video_source],
                "context_assets": [product_input, wordmark_input],
            },
            "progress_percent": 100,
            "provider_job_id": f"demo-{VIDEO_JOB_ID}",
            "attempt_count": 1,
            "error_message": None,
            "started_at": VIDEO_V1.recorded_at.replace(minute=0),
            "completed_at": VIDEO_V1.recorded_at,
        },
    )
    increment_count(counts, "generation_jobs", created)
    return counts


def get_showcase_campaign_for_export(db: Session) -> Campaign:
    campaign = db.scalar(
        select(Campaign)
        .options(
            selectinload(Campaign.assets)
            .selectinload(Asset.versions)
            .selectinload(AssetVersion.inputs),
            selectinload(Campaign.brand_asset_links).selectinload(
                CampaignBrandAsset.brand_asset
            ),
        )
        .where(Campaign.id == SHOWCASE_CAMPAIGN_ID)
    )
    if campaign is None:
        raise RuntimeError("Showcase campaign was not seeded")
    return campaign
