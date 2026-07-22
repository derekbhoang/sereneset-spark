import argparse
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

from app.api.v1.routes.campaigns import write_campaign_export_zip  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.asset import Asset, AssetFormat, AssetVersion, ReviewStatus  # noqa: E402
from app.models.campaign import Campaign  # noqa: E402
from app.services.storage import B2StorageService  # noqa: E402
from scripts.demo_showcase import (  # noqa: E402
    get_showcase_campaign_for_export,
    seed_showcase_campaign,
)


CampaignSeed = dict[str, Any]
AssetSeed = dict[str, Any]
VersionSeed = dict[str, Any]


DEMO_CAMPAIGNS: list[CampaignSeed] = [
    {
        "name": "Summer Reset Launch",
        "product": "SereneSet Essentials Kit",
        "audience": "Busy wellness shoppers, 28-44",
        "status": "generating",
        "due_date": date(2026, 7, 12),
        "owner": "Mira Chen",
        "goal": "Increase waitlist signups before the retail launch.",
        "tone": "Grounded, precise, calm",
        "brief": (
            "Introduce the essentials kit as a simple daily reset for people "
            "who want a calmer home routine without a complicated ritual."
        ),
        "channels": ["Instagram", "Email", "Paid social", "Landing page"],
        "brand_inputs": ["Tone guide v3", "Product claims", "Usage disclaimers"],
        "assets": [
            {
                "title": "Five-slide launch carousel",
                "format": AssetFormat.image,
                "channel": "Instagram",
                "status": ReviewStatus.in_review,
                "reviewer": "Avery",
                "tags": ["launch", "routine", "visual"],
                "summary": (
                    "A quiet sequence that opens on a sunlit counter, moves "
                    "through three product-use moments, and closes with a "
                    "waitlist callout."
                ),
                "versions": [
                    {
                        "version_number": 1,
                        "label": "Initial carousel concept",
                        "prompt": (
                            "Create a calm wellness carousel concept for a "
                            "summer reset kit using natural light."
                        ),
                        "model": "gmi/image-campaign-v2",
                        "provider": "gmi",
                        "storage_key": (
                            "campaigns/summer-reset-launch/assets/"
                            "five-slide-launch-carousel/versions/v1/preview.png"
                        ),
                        "generation_metadata": {
                            "channel": "Instagram",
                            "format": "image",
                            "seed_source": "scripts/seed.py",
                        },
                    },
                    {
                        "version_number": 2,
                        "label": "Added daily ritual framing",
                        "prompt": (
                            "Refine the carousel to emphasize a simple daily "
                            "ritual and remove spa language."
                        ),
                        "model": "gmi/image-campaign-v2",
                        "provider": "gmi",
                        "storage_key": (
                            "campaigns/summer-reset-launch/assets/"
                            "five-slide-launch-carousel/versions/v2/preview.png"
                        ),
                        "generation_metadata": {
                            "channel": "Instagram",
                            "format": "image",
                            "seed_source": "scripts/seed.py",
                        },
                    },
                    {
                        "version_number": 3,
                        "label": "Softened product shadows and tightened CTA",
                        "prompt": (
                            "Create a calm wellness carousel concept for a "
                            "summer reset kit using natural light and concise "
                            "waitlist messaging."
                        ),
                        "model": "gmi/image-campaign-v2",
                        "provider": "gmi",
                        "storage_key": (
                            "campaigns/summer-reset-launch/assets/"
                            "five-slide-launch-carousel/versions/v3/preview.png"
                        ),
                        "generation_metadata": {
                            "channel": "Instagram",
                            "format": "image",
                            "seed_source": "scripts/seed.py",
                        },
                    },
                ],
            },
            {
                "title": "Waitlist email hero copy",
                "format": AssetFormat.copy,
                "channel": "Email",
                "status": ReviewStatus.approved,
                "reviewer": "Mira",
                "tags": ["email", "waitlist", "approved"],
                "summary": (
                    "A calmer routine starts with fewer decisions. Meet the "
                    "SereneSet Essentials Kit, a compact edit for resetting "
                    "the tone of your space."
                ),
                "versions": [
                    {
                        "version_number": 1,
                        "label": "Approved headline and preheader",
                        "prompt": (
                            "Write email hero copy for a wellness kit launch "
                            "with calm, grounded language and no clinical claims."
                        ),
                        "model": "gpt-4.1",
                        "provider": "openai",
                        "storage_key": (
                            "campaigns/summer-reset-launch/assets/"
                            "waitlist-email-hero-copy/versions/v1/copy.json"
                        ),
                        "generation_metadata": {
                            "channel": "Email",
                            "format": "copy",
                            "seed_source": "scripts/seed.py",
                        },
                    },
                ],
            },
            {
                "title": "Paid social concept trio",
                "format": AssetFormat.image,
                "channel": "Paid social",
                "status": ReviewStatus.draft,
                "reviewer": None,
                "tags": ["paid", "concept", "testing"],
                "summary": (
                    "Three square concepts test product-first, routine-first, "
                    "and offer-first positioning for paid social acquisition."
                ),
                "versions": [
                    {
                        "version_number": 1,
                        "label": "Initial testing concepts",
                        "prompt": (
                            "Generate three paid social image concepts with "
                            "distinct positioning angles for the essentials kit."
                        ),
                        "model": "gmi/image-campaign-v2",
                        "provider": "gmi",
                        "storage_key": (
                            "campaigns/summer-reset-launch/assets/"
                            "paid-social-concept-trio/versions/v1/concepts.json"
                        ),
                        "generation_metadata": {
                            "channel": "Paid social",
                            "format": "image",
                            "seed_source": "scripts/seed.py",
                        },
                    },
                ],
            },
        ],
    },
    {
        "name": "Retail Partner Pitch",
        "product": "Wholesale discovery pack",
        "audience": "Boutique buyers and store owners",
        "status": "review",
        "due_date": date(2026, 7, 18),
        "owner": "Noah Patel",
        "goal": "Create polished sell-in assets for three regional buyers.",
        "tone": "Commercial, clear, assured",
        "brief": (
            "Turn the wholesale pack into concise sales assets that show "
            "margin potential, shelf appeal, and repeat purchase hooks."
        ),
        "channels": ["Pitch deck", "Email", "One-sheet"],
        "brand_inputs": ["Wholesale FAQ", "Retail photography", "Margin notes"],
        "assets": [
            {
                "title": "Buyer outreach sequence",
                "format": AssetFormat.copy,
                "channel": "Email",
                "status": ReviewStatus.in_review,
                "reviewer": "Noah",
                "tags": ["retail", "buyer", "sequence"],
                "summary": (
                    "A three-touch outreach flow that leads with category fit, "
                    "follows with visual merchandising, and closes on low-risk "
                    "trial terms."
                ),
                "versions": [
                    {
                        "version_number": 1,
                        "label": "Initial wholesale flow",
                        "prompt": (
                            "Draft a three-email buyer outreach sequence for "
                            "boutique retailers evaluating a premium wellness "
                            "discovery pack."
                        ),
                        "model": "gpt-4.1",
                        "provider": "openai",
                        "storage_key": (
                            "campaigns/retail-partner-pitch/assets/"
                            "buyer-outreach-sequence/versions/v1/copy.json"
                        ),
                        "generation_metadata": {
                            "channel": "Email",
                            "format": "copy",
                            "seed_source": "scripts/seed.py",
                        },
                    },
                ],
            }
        ],
    },
    {
        "name": "Membership Refresh",
        "product": "SereneSet Circle",
        "audience": "Existing customers and dormant subscribers",
        "status": "drafting",
        "due_date": date(2026, 8, 3),
        "owner": "Lena Ortiz",
        "goal": "Reposition the monthly membership around flexible routines.",
        "tone": "Warm, practical, lightly editorial",
        "brief": (
            "Refresh membership messages so returning customers understand "
            "the value of choice, replenishment, and seasonal edits."
        ),
        "channels": ["Email", "SMS", "Customer portal"],
        "brand_inputs": ["Lifecycle segments", "Offer rules", "Voice samples"],
        "assets": [
            {
                "title": "Dormant member SMS set",
                "format": AssetFormat.copy,
                "channel": "SMS",
                "status": ReviewStatus.draft,
                "reviewer": "Lena",
                "tags": ["sms", "retention", "membership"],
                "summary": (
                    "Short retention messages that frame membership as "
                    "flexible replenishment instead of a fixed subscription."
                ),
                "versions": [
                    {
                        "version_number": 1,
                        "label": "Initial retention messages",
                        "prompt": (
                            "Write five concise SMS options for dormant wellness "
                            "subscribers with a practical, warm tone."
                        ),
                        "model": "gpt-4.1",
                        "provider": "openai",
                        "storage_key": (
                            "campaigns/membership-refresh/assets/"
                            "dormant-member-sms-set/versions/v1/copy.json"
                        ),
                        "generation_metadata": {
                            "channel": "SMS",
                            "format": "copy",
                            "seed_source": "scripts/seed.py",
                        },
                    },
                ],
            }
        ],
    },
]


TEST_CAMPAIGN_NAMES = {"string", "abc", "def"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed demo campaign data.")
    parser.add_argument(
        "--delete-test-records",
        action="store_true",
        help="Delete obvious throwaway campaigns named string, abc, or def.",
    )
    parser.add_argument(
        "--showcase-only",
        action="store_true",
        help="Skip the legacy metadata-only campaigns.",
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help=(
            "Write showcase rows without uploading fixtures to B2. The showcase "
            "will not be previewable or exportable until seeded again without "
            "this flag."
        ),
    )
    parser.add_argument(
        "--export-pack",
        type=Path,
        help="Also write the seeded showcase campaign export to this ZIP path.",
    )
    return parser.parse_args()


def set_fields(model: object, values: dict[str, Any]) -> None:
    for key, value in values.items():
        setattr(model, key, value)


def delete_test_records(db: Session) -> int:
    campaigns = db.scalars(
        select(Campaign).where(Campaign.name.in_(TEST_CAMPAIGN_NAMES))
    ).all()

    for campaign in campaigns:
        db.delete(campaign)

    return len(campaigns)


def upsert_campaign(db: Session, data: CampaignSeed) -> tuple[Campaign, bool]:
    assets = data.pop("assets")
    campaign = db.scalar(select(Campaign).where(Campaign.name == data["name"]))
    created = campaign is None

    if campaign is None:
        campaign = Campaign(**data)
        db.add(campaign)
    else:
        set_fields(campaign, data)

    db.flush()
    data["assets"] = assets

    return campaign, created


def upsert_asset(
    db: Session,
    campaign: Campaign,
    data: AssetSeed,
) -> tuple[Asset, bool]:
    versions = data.pop("versions")
    asset = db.scalar(
        select(Asset).where(
            Asset.campaign_id == campaign.id,
            Asset.title == data["title"],
        )
    )
    created = asset is None

    if asset is None:
        asset = Asset(campaign_id=campaign.id, **data)
        db.add(asset)
    else:
        set_fields(asset, data)

    db.flush()
    data["versions"] = versions

    return asset, created


def upsert_version(
    db: Session,
    asset: Asset,
    data: VersionSeed,
) -> tuple[AssetVersion, bool]:
    version = db.scalar(
        select(AssetVersion).where(
            AssetVersion.asset_id == asset.id,
            AssetVersion.version_number == data["version_number"],
        )
    )
    created = version is None

    if version is None:
        version = AssetVersion(asset_id=asset.id, **data)
        db.add(version)
    else:
        set_fields(version, data)

    return version, created


def merge_counts(
    counts: dict[str, int],
    additional_counts: dict[str, int],
) -> None:
    for key, value in additional_counts.items():
        counts[key] = counts.get(key, 0) + value


def seed_demo_data(
    db: Session,
    *,
    storage: B2StorageService | None,
    include_legacy_campaigns: bool = True,
) -> dict[str, int]:
    counts = {
        "campaigns_created": 0,
        "campaigns_updated": 0,
        "assets_created": 0,
        "assets_updated": 0,
        "versions_created": 0,
        "versions_updated": 0,
    }

    if include_legacy_campaigns:
        for campaign_data in DEMO_CAMPAIGNS:
            campaign, campaign_created = upsert_campaign(db, campaign_data)
            counts[
                "campaigns_created" if campaign_created else "campaigns_updated"
            ] += 1

            for asset_data in campaign_data["assets"]:
                asset, asset_created = upsert_asset(db, campaign, asset_data)
                counts["assets_created" if asset_created else "assets_updated"] += 1

                for version_data in asset_data["versions"]:
                    _, version_created = upsert_version(db, asset, version_data)
                    counts[
                        "versions_created" if version_created else "versions_updated"
                    ] += 1

    merge_counts(
        counts,
        seed_showcase_campaign(db, storage=storage),
    )

    return counts


def main() -> None:
    args = parse_args()
    if args.metadata_only and args.export_pack is not None:
        raise SystemExit("--export-pack cannot be used with --metadata-only")

    settings = get_settings()
    storage = None
    if not args.metadata_only:
        storage = B2StorageService(settings)
        storage.check_bucket_access()

    with SessionLocal() as db:
        deleted_test_records = 0

        if args.delete_test_records:
            deleted_test_records = delete_test_records(db)

        counts = seed_demo_data(
            db,
            storage=storage,
            include_legacy_campaigns=not args.showcase_only,
        )
        db.commit()

    export_path: Path | None = None
    if args.export_pack is not None:
        assert storage is not None
        export_path = args.export_pack.resolve()
        export_path.parent.mkdir(parents=True, exist_ok=True)
        with SessionLocal() as db:
            campaign = get_showcase_campaign_for_export(db)
            write_campaign_export_zip(
                campaign=campaign,
                storage=storage,
                destination=str(export_path),
                max_video_artifact_size_bytes=(settings.max_generated_video_size_bytes),
                max_non_video_input_size_bytes=(
                    settings.max_video_source_image_size_bytes
                ),
                max_video_input_size_bytes=(
                    settings.max_video_source_video_size_bytes
                ),
            )

    print("Seed complete")
    if args.metadata_only:
        print("B2 uploads skipped; rerun without --metadata-only before demoing")
    if args.delete_test_records:
        print(f"Deleted test campaigns: {deleted_test_records}")
    if export_path is not None:
        print(f"Export pack: {export_path}")

    for key, value in counts.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
