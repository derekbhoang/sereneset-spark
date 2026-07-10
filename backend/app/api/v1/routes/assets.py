import hashlib
import mimetypes
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.error import URLError
from urllib.request import Request, urlopen

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.asset import Asset, AssetVersion, AssetVersionInput, ReviewStatus
from app.models.brand_asset import BrandAsset, CampaignBrandAsset
from app.models.campaign import Campaign
from app.schemas.asset import (
    AssetCreate,
    AssetGenerationCreate,
    AssetRead,
    AssetStatusUpdate,
    AssetVersionArtifactDownloadUrl,
    AssetVersionCreate,
    AssetVersionDownloadUrl,
    AssetVersionGenerationCreate,
    AssetVersionRead,
)
from app.services.generation import (
    GeneratedAsset,
    GenerationConfigurationError,
    GenerationProviderError,
    GenerationResult,
    GenblazeGenerationService,
    ImageGenerationRequest,
    get_generation_service,
)
from app.services.storage import (
    B2StorageService,
    StorageConfigurationError,
    build_asset_version_artifact_storage_key,
    build_asset_version_input_storage_key,
    build_asset_version_storage_key,
    get_storage_service,
    normalize_asset_version_input_role,
    normalize_artifact_filename,
)


router = APIRouter(tags=["assets"])
MAX_ARTIFACT_SIZE_BYTES = 25 * 1024 * 1024
MAX_GENERATION_INPUT_FILES = 5
MAX_GENERATION_INPUT_SIZE_BYTES = 25 * 1024 * 1024
GENERATION_INPUT_DOWNLOAD_URL_EXPIRES_SECONDS = 3600
DEFAULT_GENERATION_INPUT_ROLE = "style_reference"
ALLOWED_GENERATION_INPUT_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}
ALLOWED_GENERATION_INPUT_ROLES = {
    "avoid_reference",
    "brand_reference",
    "product",
    "source_creative",
    "style_reference",
}


@dataclass(frozen=True)
class GeneratedArtifactPayload:
    body: bytes
    content_type: str | None


@dataclass(frozen=True)
class MultipartGenerationInput:
    file: UploadFile
    role: str
    filename: str
    content_type: str
    size_bytes: int


@dataclass(frozen=True)
class StoredGenerationInput:
    role: str
    storage_key: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    source: str = "user_upload"
    owns_storage_object: bool = True
    brand_asset_id: uuid.UUID | None = None
    campaign_brand_asset_id: uuid.UUID | None = None
    brand_asset_type: str | None = None
    brand_asset_name: str | None = None
    usage_guidance: str | None = None


def parse_asset_generation_payload(payload: str) -> AssetGenerationCreate:
    try:
        return AssetGenerationCreate.model_validate_json(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc


def parse_asset_version_generation_payload(
    payload: str,
) -> AssetVersionGenerationCreate:
    try:
        return AssetVersionGenerationCreate.model_validate_json(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc


def parse_multipart_generation_inputs(
    *,
    files: list[UploadFile] | None,
    roles: list[str] | None,
) -> list[MultipartGenerationInput]:
    input_files = files or []
    input_roles = roles or []

    if not input_files:
        if input_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Roles were provided without input files",
            )

        return []

    if len(input_files) > MAX_GENERATION_INPUT_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Generation input images are limited to {MAX_GENERATION_INPUT_FILES}",
        )

    if not input_roles:
        input_roles = [DEFAULT_GENERATION_INPUT_ROLE] * len(input_files)
    elif len(input_roles) != len(input_files):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Roles must include one value for each input file",
        )

    return [
        validate_multipart_generation_input(file=file, role=role)
        for file, role in zip(input_files, input_roles, strict=True)
    ]


def normalize_generation_input_content_type(file: UploadFile) -> str:
    content_type = (file.content_type or "").split(";")[0].strip().lower()

    if content_type not in ALLOWED_GENERATION_INPUT_CONTENT_TYPES:
        allowed_types = ", ".join(sorted(ALLOWED_GENERATION_INPUT_CONTENT_TYPES))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Input image '{file.filename or 'unnamed'}' must use one of: "
                f"{allowed_types}"
            ),
        )

    return content_type


def normalize_generation_input_filename(file: UploadFile) -> str:
    try:
        return normalize_artifact_filename(file.filename or "")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc).replace("Artifact", "Input image"),
        ) from exc


def normalize_generation_input_role(role: str) -> str:
    try:
        normalized_role = normalize_asset_version_input_role(role)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if normalized_role not in ALLOWED_GENERATION_INPUT_ROLES:
        allowed_roles = ", ".join(sorted(ALLOWED_GENERATION_INPUT_ROLES))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Input role must be one of: {allowed_roles}",
        )

    return normalized_role


def read_generation_input_for_validation(file: UploadFile) -> bytes:
    file.file.seek(0)
    content = file.file.read(MAX_GENERATION_INPUT_SIZE_BYTES + 1)
    file.file.seek(0)

    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Input image '{file.filename or 'unnamed'}' must not be empty",
        )

    if len(content) > MAX_GENERATION_INPUT_SIZE_BYTES:
        max_megabytes = MAX_GENERATION_INPUT_SIZE_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Input image '{file.filename or 'unnamed'}' must be "
                f"{max_megabytes} MB or smaller"
            ),
        )

    return content


def validate_multipart_generation_input(
    *,
    file: UploadFile,
    role: str,
) -> MultipartGenerationInput:
    filename = normalize_generation_input_filename(file)
    normalized_role = normalize_generation_input_role(role)
    content_type = normalize_generation_input_content_type(file)
    content = read_generation_input_for_validation(file)

    return MultipartGenerationInput(
        file=file,
        role=normalized_role,
        filename=filename,
        content_type=content_type,
        size_bytes=len(content),
    )


def deduplicate_generation_input_filename(filename: str, occurrence: int) -> str:
    if occurrence <= 1:
        return filename

    stem, separator, suffix = filename.rpartition(".")
    if separator and stem:
        return f"{stem}-{occurrence}.{suffix}"

    return f"{filename}-{occurrence}"


def upload_generation_input(
    *,
    storage: B2StorageService,
    campaign: Campaign,
    asset: Asset,
    version_number: int,
    generation_input: MultipartGenerationInput,
    filename: str,
) -> StoredGenerationInput:
    body = read_generation_input_for_validation(generation_input.file)
    sha256 = hashlib.sha256(body).hexdigest()
    storage_key = build_asset_version_input_storage_key(
        campaign_id=asset.campaign_id,
        asset_id=asset.id,
        version_number=version_number,
        role=generation_input.role,
        filename=filename,
    )
    stored_object = storage.upload_bytes(
        key=storage_key,
        body=body,
        content_type=generation_input.content_type,
        metadata={
            "campaign_id": str(campaign.id),
            "asset_id": str(asset.id),
            "version_number": version_number,
            "content_kind": "asset-version-input",
            "role": generation_input.role,
            "filename": filename,
            "sha256": sha256,
        },
    )

    return StoredGenerationInput(
        role=generation_input.role,
        storage_key=stored_object.key,
        filename=filename,
        content_type=stored_object.content_type,
        size_bytes=stored_object.size,
        sha256=sha256,
    )


def upload_generation_inputs(
    *,
    storage: B2StorageService,
    campaign: Campaign,
    asset: Asset,
    version_number: int,
    generation_inputs: list[MultipartGenerationInput],
) -> list[StoredGenerationInput]:
    stored_inputs: list[StoredGenerationInput] = []
    filename_counts: dict[tuple[str, str], int] = {}

    try:
        for generation_input in generation_inputs:
            filename_key = (generation_input.role, generation_input.filename)
            filename_counts[filename_key] = filename_counts.get(filename_key, 0) + 1
            filename = deduplicate_generation_input_filename(
                generation_input.filename,
                filename_counts[filename_key],
            )
            stored_inputs.append(
                upload_generation_input(
                    storage=storage,
                    campaign=campaign,
                    asset=asset,
                    version_number=version_number,
                    generation_input=generation_input,
                    filename=filename,
                )
            )
    except (StorageConfigurationError, BotoCoreError, ClientError):
        delete_stored_inputs(storage=storage, stored_inputs=stored_inputs)
        raise

    return stored_inputs


def add_asset_version_inputs(
    *,
    db: Session,
    version: AssetVersion,
    stored_inputs: list[StoredGenerationInput],
) -> None:
    for stored_input in stored_inputs:
        db.add(
            AssetVersionInput(
                asset_version_id=version.id,
                role=stored_input.role,
                storage_key=stored_input.storage_key,
                filename=stored_input.filename,
                content_type=stored_input.content_type,
                size_bytes=stored_input.size_bytes,
                sha256=stored_input.sha256,
            )
        )


def stored_generation_input_to_metadata(
    stored_input: StoredGenerationInput,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "role": stored_input.role,
        "storage_key": stored_input.storage_key,
        "filename": stored_input.filename,
        "content_type": stored_input.content_type,
        "size_bytes": stored_input.size_bytes,
        "sha256": stored_input.sha256,
        "source": stored_input.source,
        "storage_ownership": (
            "asset_version" if stored_input.owns_storage_object else "brand_asset"
        ),
    }

    optional_metadata = {
        "brand_asset_id": stored_input.brand_asset_id,
        "campaign_brand_asset_id": stored_input.campaign_brand_asset_id,
        "brand_asset_type": stored_input.brand_asset_type,
        "brand_asset_name": stored_input.brand_asset_name,
        "usage_guidance": stored_input.usage_guidance,
    }
    metadata.update(
        {
            key: str(value) if isinstance(value, uuid.UUID) else value
            for key, value in optional_metadata.items()
            if value is not None
        }
    )
    return metadata


def stored_generation_input_to_request_asset(
    *,
    storage: B2StorageService,
    stored_input: StoredGenerationInput,
) -> dict[str, object]:
    return {
        **stored_generation_input_to_metadata(stored_input),
        "url": storage.generate_presigned_download_url(
            key=stored_input.storage_key,
            expires_seconds=GENERATION_INPUT_DOWNLOAD_URL_EXPIRES_SECONDS,
        ),
    }


def stored_generation_inputs_to_metadata(
    stored_inputs: list[StoredGenerationInput],
) -> list[dict[str, object]]:
    return [
        stored_generation_input_to_metadata(stored_input)
        for stored_input in stored_inputs
    ]


def stored_generation_inputs_to_request_assets(
    *,
    storage: B2StorageService,
    stored_inputs: list[StoredGenerationInput],
) -> list[dict[str, object]]:
    return [
        stored_generation_input_to_request_asset(
            storage=storage,
            stored_input=stored_input,
        )
        for stored_input in stored_inputs
    ]


def is_image_asset_descriptor(
    *,
    content_type: str | None,
    filename: str | None,
) -> bool:
    normalized_content_type = (content_type or "").split(";")[0].strip().lower()
    if normalized_content_type.startswith("image/"):
        return True

    guessed_content_type, _encoding = mimetypes.guess_type(filename or "")
    return bool(guessed_content_type and guessed_content_type.startswith("image/"))


def infer_content_type_from_filename(
    *,
    content_type: str | None,
    filename: str | None,
    default: str = "application/octet-stream",
) -> str:
    normalized_content_type = (content_type or "").split(";")[0].strip().lower()
    guessed_content_type, _encoding = mimetypes.guess_type(filename or "")

    if (
        guessed_content_type
        and (
            not normalized_content_type
            or normalized_content_type == "application/octet-stream"
        )
    ):
        return guessed_content_type

    return normalized_content_type or default


def campaign_brand_asset_generation_inputs(
    *,
    campaign_id: uuid.UUID,
    db: Session,
) -> list[StoredGenerationInput]:
    statement = (
        select(CampaignBrandAsset)
        .join(CampaignBrandAsset.brand_asset)
        .options(selectinload(CampaignBrandAsset.brand_asset))
        .where(
            CampaignBrandAsset.campaign_id == campaign_id,
            BrandAsset.is_active.is_(True),
        )
        .order_by(CampaignBrandAsset.created_at.asc())
    )
    links = list(db.scalars(statement).all())
    generation_inputs: list[StoredGenerationInput] = []
    unsupported_image_filenames: list[str] = []

    for link in links:
        brand_asset = link.brand_asset
        content_type = infer_content_type_from_filename(
            content_type=brand_asset.content_type,
            filename=brand_asset.filename,
        )
        if content_type not in ALLOWED_GENERATION_INPUT_CONTENT_TYPES:
            if content_type.startswith("image/"):
                unsupported_image_filenames.append(brand_asset.filename)
            continue

        generation_inputs.append(
            StoredGenerationInput(
                role=link.role,
                storage_key=brand_asset.storage_key,
                filename=brand_asset.filename,
                content_type=content_type,
                size_bytes=brand_asset.size_bytes,
                sha256=brand_asset.sha256,
                source="campaign_brand_asset",
                owns_storage_object=False,
                brand_asset_id=brand_asset.id,
                campaign_brand_asset_id=link.id,
                brand_asset_type=brand_asset.asset_type.value,
                brand_asset_name=brand_asset.name,
                usage_guidance=brand_asset.usage_guidance,
            )
        )

    if unsupported_image_filenames:
        filenames = ", ".join(unsupported_image_filenames)
        allowed_types = ", ".join(sorted(ALLOWED_GENERATION_INPUT_CONTENT_TYPES))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Attached brand images must use one of: {allowed_types}. "
                f"Replace or detach: {filenames}"
            ),
        )

    return generation_inputs


def validate_generation_input_count(input_count: int) -> None:
    if input_count <= MAX_GENERATION_INPUT_FILES:
        return

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "Generation accepts at most "
            f"{MAX_GENERATION_INPUT_FILES} image inputs across the latest version, "
            "uploaded files, and attached campaign brand assets"
        ),
    )


def latest_version_artifact_input_metadata(
    latest_version: AssetVersion | None,
) -> list[dict[str, object]]:
    if latest_version is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Asset does not have a latest version to refine from",
        )

    if latest_version.artifact_storage_key is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Latest version does not have a stored artifact to refine from",
        )

    if not is_image_asset_descriptor(
        content_type=latest_version.artifact_content_type,
        filename=latest_version.artifact_filename,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Latest version artifact must be an image to refine from",
        )

    artifact_flow = optional_metadata_dict(
        (latest_version.generation_metadata or {}).get("artifact_flow")
    )
    artifact_filename = (
        latest_version.artifact_filename
        or f"version-{latest_version.version_number}-artifact"
    )
    artifact_content_type = infer_content_type_from_filename(
        content_type=latest_version.artifact_content_type,
        filename=artifact_filename,
        default="image/png",
    )
    return [
        {
            "role": "source_creative",
            "storage_key": latest_version.artifact_storage_key,
            "filename": artifact_filename,
            "content_type": artifact_content_type,
            "size_bytes": latest_version.artifact_size_bytes,
            "sha256": optional_string(artifact_flow.get("source_sha256")),
            "source": "latest_version_artifact",
            "source_version_id": str(latest_version.id),
            "source_version_number": latest_version.version_number,
        }
    ]


def latest_version_artifact_request_assets(
    *,
    storage: B2StorageService,
    latest_version: AssetVersion | None,
) -> list[dict[str, object]]:
    return [
        {
            **input_asset,
            "url": storage.generate_presigned_download_url(
                key=str(input_asset["storage_key"]),
                expires_seconds=GENERATION_INPUT_DOWNLOAD_URL_EXPIRES_SECONDS,
            ),
        }
        for input_asset in latest_version_artifact_input_metadata(latest_version)
    ]


def input_asset_fingerprint(input_asset: dict[str, object | None]) -> str:
    storage_key = optional_string(input_asset.get("storage_key"))
    if storage_key:
        return f"storage:{storage_key}"

    url = optional_string(input_asset.get("url"))
    if url:
        return f"url:{url}"

    role = optional_string(input_asset.get("role")) or "reference"
    filename = optional_string(input_asset.get("filename")) or "input"
    return f"name:{role}:{filename}"


def merge_input_asset_records(
    *input_asset_groups: list[dict[str, object | None]],
) -> list[dict[str, object | None]]:
    merged_input_assets: list[dict[str, object | None]] = []
    seen_input_assets: set[str] = set()

    for input_asset_group in input_asset_groups:
        for input_asset in input_asset_group:
            fingerprint = input_asset_fingerprint(input_asset)
            if fingerprint in seen_input_assets:
                continue

            seen_input_assets.add(fingerprint)
            merged_input_assets.append(input_asset)

    return merged_input_assets


def get_asset_or_404(asset_id: uuid.UUID, db: Session) -> Asset:
    statement = (
        select(Asset)
        .options(selectinload(Asset.versions).selectinload(AssetVersion.inputs))
        .where(Asset.id == asset_id)
    )
    asset = db.scalar(statement)
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    return asset


def get_asset_version_or_404(
    *,
    asset_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session,
) -> AssetVersion:
    statement = (
        select(AssetVersion)
        .options(selectinload(AssetVersion.inputs))
        .where(
            AssetVersion.id == version_id,
            AssetVersion.asset_id == asset_id,
        )
    )
    version = db.scalar(statement)
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset version not found",
        )

    return version


def ensure_campaign_exists(campaign_id: uuid.UUID, db: Session) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )

    return campaign


def make_asset_version(
    *,
    asset: Asset,
    version_in: AssetVersionCreate,
) -> AssetVersion:
    version_data = version_in.model_dump()
    version_data["generation_metadata"] = build_version_generation_metadata(
        provider=version_in.provider,
        model=version_in.model,
        prompt=version_in.prompt,
        base_metadata=version_in.generation_metadata,
        source="manual_asset_version_create",
    )
    storage_key = build_asset_version_storage_key(
        campaign_id=asset.campaign_id,
        asset_id=asset.id,
        version_number=version_in.version_number,
    )

    return AssetVersion(
        asset_id=asset.id,
        storage_key=storage_key,
        **version_data,
    )


def make_generated_asset_version(
    *,
    asset: Asset,
    version_number: int,
    label: str,
    prompt: str,
    result: GenerationResult,
    generation_parameters: dict[str, object],
    source: str,
    based_on_version_id: uuid.UUID | None = None,
    input_assets: list[dict[str, object]] | None = None,
) -> AssetVersion:
    version = AssetVersion(
        asset_id=asset.id,
        version_number=version_number,
        label=label,
        prompt=prompt,
        model=result.model,
        provider=result.provider,
        storage_key=build_asset_version_storage_key(
            campaign_id=asset.campaign_id,
            asset_id=asset.id,
            version_number=version_number,
        ),
        generation_metadata=build_generation_metadata(
            result=result,
            generation_parameters=generation_parameters,
            source=source,
            based_on_version_id=based_on_version_id,
            input_assets=input_assets,
        ),
    )

    return version


def generated_asset_to_metadata(asset: GeneratedAsset) -> dict[str, object | None]:
    return {
        "url": asset.url,
        "storage_key": asset.storage_key,
        "sha256": asset.sha256,
        "content_type": asset.content_type,
        "size_bytes": asset.size_bytes,
        "filename": asset.filename,
    }


def build_version_generation_metadata(
    *,
    provider: str,
    model: str,
    prompt: str,
    base_metadata: dict[str, object],
    source: str,
    generation_parameters: dict[str, object] | None = None,
    based_on_version_id: uuid.UUID | str | None = None,
    manifest_uri: str | None = None,
    manifest_hash: str | None = None,
    manifest_verified: bool | None = None,
    assets: list[dict[str, object | None]] | None = None,
    input_assets: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    existing_provenance = base_metadata.get("provenance")
    based_on_version = (
        str(based_on_version_id) if based_on_version_id is not None else None
    )
    input_asset_records = (
        input_assets
        if input_assets is not None
        else optional_asset_metadata_list(base_metadata.get("input_assets"))
    )
    provenance: dict[str, object] = {
        "provider": provider,
        "model": model,
        "prompt": prompt,
        "source": source,
        "based_on_version_id": based_on_version,
        "generation_parameters": generation_parameters or {},
        "manifest_uri": manifest_uri,
        "manifest_hash": manifest_hash,
        "manifest_verified": manifest_verified,
        "input_assets": input_asset_records,
        "assets": assets or [],
        "recorded_at": datetime.now(UTC).isoformat(),
    }

    if existing_provenance is not None:
        provenance["upstream_provenance"] = existing_provenance

    return {
        **base_metadata,
        "provider": provider,
        "model": model,
        "prompt": prompt,
        "source": source,
        "based_on_version_id": based_on_version,
        "generation_parameters": generation_parameters or {},
        "manifest_uri": manifest_uri,
        "manifest_hash": manifest_hash,
        "manifest_verified": manifest_verified,
        "input_assets": input_asset_records,
        "assets": assets or [],
        "provenance": provenance,
    }


def build_generation_metadata(
    *,
    result: GenerationResult,
    generation_parameters: dict[str, object],
    source: str,
    based_on_version_id: uuid.UUID | None = None,
    input_assets: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return build_version_generation_metadata(
        provider=result.provider,
        model=result.model,
        prompt=result.prompt,
        base_metadata=result.generation_metadata,
        source=source,
        generation_parameters=generation_parameters,
        based_on_version_id=based_on_version_id,
        manifest_uri=result.manifest_uri,
        manifest_hash=result.manifest_hash,
        manifest_verified=result.manifest_verified,
        assets=[generated_asset_to_metadata(asset) for asset in result.assets],
        input_assets=input_assets,
    )


def get_generated_artifact(result: GenerationResult) -> GeneratedAsset:
    for asset in result.assets:
        if asset.storage_key or asset.url:
            return asset

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Genblaze did not return an artifact URL or storage key",
    )


def guess_artifact_extension(content_type: str | None) -> str:
    if not content_type:
        return ".png"

    guessed_extension = mimetypes.guess_extension(content_type.split(";")[0].strip())
    if guessed_extension == ".jpe":
        return ".jpg"

    return guessed_extension or ".png"


def generated_artifact_filename(
    *,
    artifact: GeneratedAsset,
    version: AssetVersion,
    content_type: str | None,
) -> str:
    artifact_filename = artifact.filename or (
        f"artifact-v{version.version_number}"
        f"{guess_artifact_extension(content_type)}"
    )

    try:
        return normalize_artifact_filename(artifact_filename)
    except ValueError:
        return f"artifact-v{version.version_number}.png"


def ensure_artifact_payload_size(payload: GeneratedArtifactPayload) -> None:
    if not payload.body:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Generated artifact was empty",
        )

    if len(payload.body) > MAX_ARTIFACT_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Generated artifact must be 25 MB or smaller",
        )


def download_generated_artifact_url(url: str) -> GeneratedArtifactPayload:
    request = Request(
        url,
        headers={"User-Agent": "SereneSet-Spark/1.0"},
    )

    try:
        with urlopen(request, timeout=60) as response:
            payload = GeneratedArtifactPayload(
                body=response.read(MAX_ARTIFACT_SIZE_BYTES + 1),
                content_type=response.headers.get_content_type(),
            )
    except (OSError, URLError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Generated artifact could not be downloaded",
        ) from exc

    ensure_artifact_payload_size(payload)
    return payload


def read_generated_artifact_payload(
    *,
    storage: B2StorageService,
    artifact: GeneratedAsset,
) -> GeneratedArtifactPayload:
    if artifact.storage_key:
        payload = GeneratedArtifactPayload(
            body=storage.download_bytes(key=artifact.storage_key),
            content_type=artifact.content_type,
        )
        ensure_artifact_payload_size(payload)
        return payload

    if artifact.url:
        return download_generated_artifact_url(artifact.url)

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Genblaze did not return a readable artifact",
    )


def upload_generated_artifact_reference(
    *,
    storage: B2StorageService,
    campaign: Campaign,
    asset: Asset,
    version: AssetVersion,
    artifact: GeneratedAsset,
    source: str,
) -> str:
    payload = read_generated_artifact_payload(storage=storage, artifact=artifact)
    raw_artifact_content_type = (
        artifact.content_type
        or payload.content_type
        or "application/octet-stream"
    )
    artifact_filename = generated_artifact_filename(
        artifact=artifact,
        version=version,
        content_type=raw_artifact_content_type,
    )
    artifact_content_type = infer_content_type_from_filename(
        content_type=raw_artifact_content_type,
        filename=artifact_filename,
    )
    artifact_storage_key = build_asset_version_artifact_storage_key(
        campaign_id=asset.campaign_id,
        asset_id=asset.id,
        version_number=version.version_number,
        filename=artifact_filename,
    )
    stored_artifact = storage.upload_bytes(
        key=artifact_storage_key,
        body=payload.body,
        content_type=artifact_content_type,
        metadata={
            "campaign_id": str(campaign.id),
            "asset_id": str(asset.id),
            "version_id": str(version.id),
            "version_number": version.version_number,
            "content_kind": "asset-version-artifact",
            "filename": artifact_filename,
            "source": source,
            "source_storage_key": artifact.storage_key,
            "source_sha256": artifact.sha256,
        },
    )

    version.artifact_storage_key = stored_artifact.key
    version.artifact_filename = artifact_filename
    version.artifact_content_type = stored_artifact.content_type
    version.artifact_size_bytes = stored_artifact.size
    ensure_version_generation_metadata(version=version, source=source)
    artifact_flow = {
        "storage_key": stored_artifact.key,
        "filename": artifact_filename,
        "content_type": stored_artifact.content_type,
        "size_bytes": stored_artifact.size,
        "source": source,
        "source_storage_key": artifact.storage_key,
        "source_sha256": artifact.sha256,
    }
    provenance = version.generation_metadata.get("provenance")
    if isinstance(provenance, dict):
        provenance = {
            **provenance,
            "artifact_flow": artifact_flow,
        }

    version.generation_metadata = {
        **version.generation_metadata,
        "artifact_flow": artifact_flow,
        "provenance": provenance,
    }

    return stored_artifact.key


def upload_generated_artifact(
    *,
    storage: B2StorageService,
    campaign: Campaign,
    asset: Asset,
    version: AssetVersion,
    result: GenerationResult,
) -> str:
    return upload_generated_artifact_reference(
        storage=storage,
        campaign=campaign,
        asset=asset,
        version=version,
        artifact=get_generated_artifact(result),
        source="genblaze",
    )


def optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value

    return None


def optional_int(value: object) -> int | None:
    if isinstance(value, int):
        return value

    if isinstance(value, str) and value.isdigit():
        return int(value)

    return None


def optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value

    return None


def optional_metadata_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value

    return {}


def optional_asset_metadata_list(value: object) -> list[dict[str, object | None]]:
    if not isinstance(value, list):
        return []

    return [item for item in value if isinstance(item, dict)]


def ensure_version_generation_metadata(
    *,
    version: AssetVersion,
    source: str,
) -> None:
    metadata = version.generation_metadata or {}

    if (
        metadata.get("provider") == version.provider
        and metadata.get("model") == version.model
        and metadata.get("prompt") == version.prompt
        and isinstance(metadata.get("provenance"), dict)
    ):
        return

    generation_parameters = optional_metadata_dict(
        metadata.get("generation_parameters")
    )
    version.generation_metadata = build_version_generation_metadata(
        provider=version.provider,
        model=version.model,
        prompt=version.prompt,
        base_metadata=metadata,
        source=optional_string(metadata.get("source")) or source,
        generation_parameters=generation_parameters,
        based_on_version_id=optional_string(metadata.get("based_on_version_id")),
        manifest_uri=optional_string(metadata.get("manifest_uri")),
        manifest_hash=optional_string(metadata.get("manifest_hash")),
        manifest_verified=optional_bool(metadata.get("manifest_verified")),
        assets=optional_asset_metadata_list(metadata.get("assets")),
        input_assets=optional_asset_metadata_list(metadata.get("input_assets")),
    )


def generated_asset_from_metadata(data: object) -> GeneratedAsset | None:
    if not isinstance(data, dict):
        return None

    storage_key = optional_string(data.get("storage_key"))
    url = optional_string(data.get("url"))

    if not storage_key and not url:
        return None

    return GeneratedAsset(
        url=url,
        storage_key=storage_key,
        sha256=optional_string(data.get("sha256")),
        content_type=optional_string(data.get("content_type")),
        size_bytes=optional_int(data.get("size_bytes")),
        filename=optional_string(data.get("filename")),
    )


def asset_version_artifact_prefix(*, asset: Asset, version: AssetVersion) -> str:
    return "/".join(
        [
            "campaigns",
            str(asset.campaign_id),
            "assets",
            str(asset.id),
            "versions",
            f"v{version.version_number}",
            "artifact",
            "",
        ]
    )


def is_app_asset_version_artifact_key(
    *,
    asset: Asset,
    version: AssetVersion,
    storage_key: str | None,
) -> bool:
    return bool(
        storage_key
        and storage_key.startswith(
            asset_version_artifact_prefix(asset=asset, version=version)
        )
    )


def existing_generated_artifact_reference(
    *,
    asset: Asset,
    version: AssetVersion,
) -> GeneratedAsset:
    metadata = version.generation_metadata or {}
    artifact_flow = metadata.get("artifact_flow")

    if isinstance(artifact_flow, dict):
        source_storage_key = optional_string(artifact_flow.get("source_storage_key"))
        if source_storage_key:
            return GeneratedAsset(
                url=None,
                storage_key=source_storage_key,
                sha256=optional_string(artifact_flow.get("source_sha256")),
                content_type=version.artifact_content_type,
                size_bytes=None,
                filename=version.artifact_filename,
            )

    generated_assets = metadata.get("assets")
    if isinstance(generated_assets, list):
        for generated_asset in generated_assets:
            artifact = generated_asset_from_metadata(generated_asset)
            if artifact is not None:
                return artifact

    if (
        version.artifact_storage_key
        and not is_app_asset_version_artifact_key(
            asset=asset,
            version=version,
            storage_key=version.artifact_storage_key,
        )
    ):
        return GeneratedAsset(
            url=None,
            storage_key=version.artifact_storage_key,
            sha256=None,
            content_type=version.artifact_content_type,
            size_bytes=version.artifact_size_bytes,
            filename=version.artifact_filename,
        )

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Asset version does not have a generated artifact to ingest",
    )


def generate_image_or_502(
    *,
    generation: GenblazeGenerationService,
    prompt: str,
    model: str | None,
    generation_parameters: dict[str, object],
    timeout_seconds: int | None,
    input_assets: list[dict[str, object]] | None = None,
) -> GenerationResult:
    try:
        return generation.generate_image(
            ImageGenerationRequest(
                prompt=prompt,
                model=model,
                parameters=generation_parameters,
                timeout_seconds=timeout_seconds,
                input_assets=input_assets or [],
            )
        )
    except GenerationConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except GenerationProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


def generated_asset_title(asset_in: AssetGenerationCreate) -> str:
    if asset_in.title:
        return asset_in.title

    format_label = asset_in.format.value.replace("_", " ")
    return f"{asset_in.channel} {format_label} draft"


def generated_asset_summary(asset_in: AssetGenerationCreate) -> str:
    if asset_in.summary:
        return asset_in.summary

    return (
        "Genblaze-generated creative asset with durable B2 artifact storage "
        "and provenance metadata."
    )


def asset_version_input_to_sidecar(
    version_input: AssetVersionInput,
) -> dict[str, object]:
    return {
        "id": str(version_input.id),
        "role": version_input.role,
        "storage_key": version_input.storage_key,
        "filename": version_input.filename,
        "content_type": version_input.content_type,
        "size_bytes": version_input.size_bytes,
        "sha256": version_input.sha256,
        "source": "user_upload",
        "created_at": version_input.created_at.isoformat(),
    }


def version_input_sidecar_records(version: AssetVersion) -> list[dict[str, object]]:
    records = [
        asset_version_input_to_sidecar(version_input)
        for version_input in sorted(
            version.inputs,
            key=lambda item: item.created_at,
        )
    ]
    metadata = version.generation_metadata or {}
    return merge_input_asset_records(
        optional_asset_metadata_list(metadata.get("input_assets")),
        records,
    )


def build_asset_version_sidecar(
    *,
    campaign: Campaign,
    asset: Asset,
    version: AssetVersion,
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
            "input_assets": version_input_sidecar_records(version),
            "generation_metadata": version.generation_metadata,
        },
        "stored_at": datetime.now(UTC).isoformat(),
    }


def upload_asset_version_sidecar(
    *,
    storage: B2StorageService,
    campaign: Campaign,
    asset: Asset,
    version: AssetVersion,
) -> None:
    stored_object = storage.upload_json(
        key=version.storage_key,
        data=build_asset_version_sidecar(
            campaign=campaign,
            asset=asset,
            version=version,
        ),
        metadata={
            "campaign_id": str(campaign.id),
            "asset_id": str(asset.id),
            "version_number": version.version_number,
            "content_kind": "asset-version-sidecar",
        },
    )
    version.storage_key = stored_object.key


def read_artifact_upload(file: UploadFile) -> bytes:
    content = file.file.read(MAX_ARTIFACT_SIZE_BYTES + 1)

    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Artifact file must not be empty",
        )

    if len(content) > MAX_ARTIFACT_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Artifact file must be 25 MB or smaller",
        )

    return content


def delete_stored_artifact(
    *,
    storage: B2StorageService,
    storage_key: str | None,
) -> None:
    if storage_key is None:
        return

    try:
        storage.delete_object(key=storage_key)
    except (StorageConfigurationError, BotoCoreError, ClientError):
        pass


def delete_stored_inputs(
    *,
    storage: B2StorageService,
    stored_inputs: list[StoredGenerationInput],
) -> None:
    for stored_input in stored_inputs:
        if stored_input.owns_storage_object:
            delete_stored_artifact(
                storage=storage,
                storage_key=stored_input.storage_key,
            )


@router.get("/campaigns/{campaign_id}/assets", response_model=list[AssetRead])
def list_campaign_assets(
    campaign_id: uuid.UUID,
    db: Session = Depends(get_db),
    status_filter: ReviewStatus | None = Query(default=None, alias="status"),
    channel: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> list[Asset]:
    ensure_campaign_exists(campaign_id, db)

    statement = (
        select(Asset)
        .options(selectinload(Asset.versions).selectinload(AssetVersion.inputs))
        .where(Asset.campaign_id == campaign_id)
        .order_by(Asset.updated_at.desc())
        .offset(offset)
        .limit(limit)
    )

    if status_filter is not None:
        statement = statement.where(Asset.status == status_filter)

    if channel is not None:
        statement = statement.where(Asset.channel == channel)

    return list(db.scalars(statement).all())


@router.post(
    "/campaigns/{campaign_id}/assets/generate",
    response_model=AssetRead,
    status_code=status.HTTP_201_CREATED,
)
def generate_campaign_asset(
    campaign_id: uuid.UUID,
    asset_in: AssetGenerationCreate,
    db: Session = Depends(get_db),
    storage: B2StorageService = Depends(get_storage_service),
    generation: GenblazeGenerationService = Depends(get_generation_service),
) -> Asset:
    campaign = ensure_campaign_exists(campaign_id, db)
    brand_inputs = campaign_brand_asset_generation_inputs(
        campaign_id=campaign_id,
        db=db,
    )
    validate_generation_input_count(len(brand_inputs))

    try:
        brand_request_inputs = stored_generation_inputs_to_request_assets(
            storage=storage,
            stored_inputs=brand_inputs,
        )
    except (StorageConfigurationError, BotoCoreError, ClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Campaign brand assets could not be prepared for generation",
        ) from exc

    brand_metadata_inputs = stored_generation_inputs_to_metadata(brand_inputs)
    generation_result = generate_image_or_502(
        generation=generation,
        prompt=asset_in.prompt,
        model=asset_in.model,
        generation_parameters=asset_in.generation_parameters,
        timeout_seconds=asset_in.timeout_seconds,
        input_assets=brand_request_inputs,
    )
    asset = Asset(
        campaign_id=campaign_id,
        title=generated_asset_title(asset_in),
        format=asset_in.format,
        channel=asset_in.channel,
        status=asset_in.status,
        reviewer=asset_in.reviewer,
        tags=["genblaze", *asset_in.tags],
        summary=generated_asset_summary(asset_in),
    )
    uploaded_artifact_key: str | None = None

    try:
        db.add(asset)
        db.flush()

        version = make_generated_asset_version(
            asset=asset,
            version_number=1,
            label="Initial Genblaze draft",
            prompt=asset_in.prompt,
            result=generation_result,
            generation_parameters=asset_in.generation_parameters,
            source="backend_genblaze_generation",
            input_assets=brand_metadata_inputs,
        )
        db.add(version)
        db.flush()
        add_asset_version_inputs(
            db=db,
            version=version,
            stored_inputs=brand_inputs,
        )
        uploaded_artifact_key = upload_generated_artifact(
            storage=storage,
            campaign=campaign,
            asset=asset,
            version=version,
            result=generation_result,
        )
        upload_asset_version_sidecar(
            storage=storage,
            campaign=campaign,
            asset=asset,
            version=version,
        )
        db.commit()
    except (StorageConfigurationError, BotoCoreError, ClientError) as exc:
        db.rollback()
        delete_stored_artifact(
            storage=storage,
            storage_key=uploaded_artifact_key,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Generated asset was not saved because B2 storage failed",
        ) from exc

    return get_asset_or_404(asset.id, db)


@router.post(
    "/campaigns/{campaign_id}/assets/generate-with-inputs",
    response_model=AssetRead,
    status_code=status.HTTP_201_CREATED,
)
def generate_campaign_asset_with_inputs(
    campaign_id: uuid.UUID,
    payload: str = Form(...),
    files: list[UploadFile] | None = File(default=None),
    roles: list[str] | None = Form(default=None),
    db: Session = Depends(get_db),
    storage: B2StorageService = Depends(get_storage_service),
    generation: GenblazeGenerationService = Depends(get_generation_service),
) -> Asset:
    asset_in = parse_asset_generation_payload(payload)
    generation_inputs = parse_multipart_generation_inputs(files=files, roles=roles)
    campaign = ensure_campaign_exists(campaign_id, db)
    brand_inputs = campaign_brand_asset_generation_inputs(
        campaign_id=campaign_id,
        db=db,
    )
    validate_generation_input_count(len(generation_inputs) + len(brand_inputs))
    asset = Asset(
        campaign_id=campaign_id,
        title=generated_asset_title(asset_in),
        format=asset_in.format,
        channel=asset_in.channel,
        status=asset_in.status,
        reviewer=asset_in.reviewer,
        tags=["genblaze", *asset_in.tags],
        summary=generated_asset_summary(asset_in),
    )
    stored_inputs: list[StoredGenerationInput] = []
    uploaded_artifact_key: str | None = None

    try:
        db.add(asset)
        db.flush()
        stored_inputs = upload_generation_inputs(
            storage=storage,
            campaign=campaign,
            asset=asset,
            version_number=1,
            generation_inputs=generation_inputs,
        )
        version_inputs = [*stored_inputs, *brand_inputs]
        metadata_input_assets = stored_generation_inputs_to_metadata(version_inputs)
        request_input_assets = stored_generation_inputs_to_request_assets(
            storage=storage,
            stored_inputs=version_inputs,
        )
        generation_result = generate_image_or_502(
            generation=generation,
            prompt=asset_in.prompt,
            model=asset_in.model,
            generation_parameters=asset_in.generation_parameters,
            timeout_seconds=asset_in.timeout_seconds,
            input_assets=request_input_assets,
        )
        version = make_generated_asset_version(
            asset=asset,
            version_number=1,
            label="Initial Genblaze draft",
            prompt=asset_in.prompt,
            result=generation_result,
            generation_parameters=asset_in.generation_parameters,
            source="backend_genblaze_generation",
            input_assets=metadata_input_assets,
        )
        db.add(version)
        db.flush()
        add_asset_version_inputs(
            db=db,
            version=version,
            stored_inputs=version_inputs,
        )
        uploaded_artifact_key = upload_generated_artifact(
            storage=storage,
            campaign=campaign,
            asset=asset,
            version=version,
            result=generation_result,
        )
        upload_asset_version_sidecar(
            storage=storage,
            campaign=campaign,
            asset=asset,
            version=version,
        )
        db.commit()
    except HTTPException:
        db.rollback()
        delete_stored_inputs(storage=storage, stored_inputs=stored_inputs)
        delete_stored_artifact(storage=storage, storage_key=uploaded_artifact_key)
        raise
    except IntegrityError as exc:
        db.rollback()
        delete_stored_inputs(storage=storage, stored_inputs=stored_inputs)
        delete_stored_artifact(storage=storage, storage_key=uploaded_artifact_key)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Generated asset could not be saved because database constraints failed",
        ) from exc
    except (StorageConfigurationError, BotoCoreError, ClientError) as exc:
        db.rollback()
        delete_stored_inputs(storage=storage, stored_inputs=stored_inputs)
        delete_stored_artifact(storage=storage, storage_key=uploaded_artifact_key)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Generated asset was not saved because B2 storage failed",
        ) from exc

    return get_asset_or_404(asset.id, db)


@router.post(
    "/assets/{asset_id}/versions/generate",
    response_model=AssetRead,
    status_code=status.HTTP_201_CREATED,
)
def generate_asset_version(
    asset_id: uuid.UUID,
    version_in: AssetVersionGenerationCreate,
    db: Session = Depends(get_db),
    storage: B2StorageService = Depends(get_storage_service),
    generation: GenblazeGenerationService = Depends(get_generation_service),
) -> Asset:
    asset = get_asset_or_404(asset_id, db)
    campaign = ensure_campaign_exists(asset.campaign_id, db)
    latest_version = max(
        asset.versions,
        key=lambda asset_version: asset_version.version_number,
        default=None,
    )
    version_number = (latest_version.version_number if latest_version else 0) + 1
    latest_metadata_input_assets = latest_version_artifact_input_metadata(
        latest_version
    )
    brand_inputs = campaign_brand_asset_generation_inputs(
        campaign_id=campaign.id,
        db=db,
    )
    validate_generation_input_count(
        len(latest_metadata_input_assets) + len(brand_inputs)
    )
    metadata_input_assets = merge_input_asset_records(
        latest_metadata_input_assets,
        stored_generation_inputs_to_metadata(brand_inputs),
    )
    try:
        request_input_assets = [
            *latest_version_artifact_request_assets(
                storage=storage,
                latest_version=latest_version,
            ),
            *stored_generation_inputs_to_request_assets(
                storage=storage,
                stored_inputs=brand_inputs,
            ),
        ]
    except (StorageConfigurationError, BotoCoreError, ClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Latest version artifact or campaign brand assets could not be "
                "prepared as generation inputs"
            ),
        ) from exc

    generation_result = generate_image_or_502(
        generation=generation,
        prompt=version_in.prompt,
        model=version_in.model,
        generation_parameters=version_in.generation_parameters,
        timeout_seconds=version_in.timeout_seconds,
        input_assets=request_input_assets,
    )
    version = make_generated_asset_version(
        asset=asset,
        version_number=version_number,
        label=version_in.label or f"Genblaze refinement {version_number}",
        prompt=version_in.prompt,
        result=generation_result,
        generation_parameters=version_in.generation_parameters,
        source="backend_genblaze_refinement",
        based_on_version_id=latest_version.id if latest_version else None,
        input_assets=metadata_input_assets,
    )
    uploaded_artifact_key: str | None = None

    try:
        db.add(version)
        asset.updated_at = datetime.now(UTC)
        db.flush()
        add_asset_version_inputs(
            db=db,
            version=version,
            stored_inputs=brand_inputs,
        )
        uploaded_artifact_key = upload_generated_artifact(
            storage=storage,
            campaign=campaign,
            asset=asset,
            version=version,
            result=generation_result,
        )
        upload_asset_version_sidecar(
            storage=storage,
            campaign=campaign,
            asset=asset,
            version=version,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        delete_stored_artifact(
            storage=storage,
            storage_key=uploaded_artifact_key,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Asset version number already exists",
        ) from exc
    except (StorageConfigurationError, BotoCoreError, ClientError) as exc:
        db.rollback()
        delete_stored_artifact(
            storage=storage,
            storage_key=uploaded_artifact_key,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Generated asset version was not saved because B2 storage failed",
        ) from exc

    return get_asset_or_404(asset.id, db)


@router.post(
    "/assets/{asset_id}/versions/generate-with-inputs",
    response_model=AssetRead,
    status_code=status.HTTP_201_CREATED,
)
def generate_asset_version_with_inputs(
    asset_id: uuid.UUID,
    payload: str = Form(...),
    files: list[UploadFile] | None = File(default=None),
    roles: list[str] | None = Form(default=None),
    db: Session = Depends(get_db),
    storage: B2StorageService = Depends(get_storage_service),
    generation: GenblazeGenerationService = Depends(get_generation_service),
) -> Asset:
    version_in = parse_asset_version_generation_payload(payload)
    generation_inputs = parse_multipart_generation_inputs(files=files, roles=roles)
    asset = get_asset_or_404(asset_id, db)
    campaign = ensure_campaign_exists(asset.campaign_id, db)
    latest_version = max(
        asset.versions,
        key=lambda asset_version: asset_version.version_number,
        default=None,
    )
    version_number = (latest_version.version_number if latest_version else 0) + 1
    latest_metadata_input_assets = latest_version_artifact_input_metadata(
        latest_version
    )
    brand_inputs = campaign_brand_asset_generation_inputs(
        campaign_id=campaign.id,
        db=db,
    )
    validate_generation_input_count(
        len(latest_metadata_input_assets)
        + len(generation_inputs)
        + len(brand_inputs)
    )
    try:
        latest_request_input_assets = latest_version_artifact_request_assets(
            storage=storage,
            latest_version=latest_version,
        )
    except (StorageConfigurationError, BotoCoreError, ClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Latest version artifact could not be prepared as a generation input",
        ) from exc

    stored_inputs: list[StoredGenerationInput] = []
    uploaded_artifact_key: str | None = None

    try:
        stored_inputs = upload_generation_inputs(
            storage=storage,
            campaign=campaign,
            asset=asset,
            version_number=version_number,
            generation_inputs=generation_inputs,
        )
        version_inputs = [*stored_inputs, *brand_inputs]
        metadata_input_assets = merge_input_asset_records(
            latest_metadata_input_assets,
            stored_generation_inputs_to_metadata(stored_inputs),
            stored_generation_inputs_to_metadata(brand_inputs),
        )
        uploaded_request_input_assets = stored_generation_inputs_to_request_assets(
            storage=storage,
            stored_inputs=stored_inputs,
        )
        request_input_assets = [
            *latest_request_input_assets,
            *uploaded_request_input_assets,
            *stored_generation_inputs_to_request_assets(
                storage=storage,
                stored_inputs=brand_inputs,
            ),
        ]
        generation_result = generate_image_or_502(
            generation=generation,
            prompt=version_in.prompt,
            model=version_in.model,
            generation_parameters=version_in.generation_parameters,
            timeout_seconds=version_in.timeout_seconds,
            input_assets=request_input_assets,
        )
        version = make_generated_asset_version(
            asset=asset,
            version_number=version_number,
            label=version_in.label or f"Genblaze refinement {version_number}",
            prompt=version_in.prompt,
            result=generation_result,
            generation_parameters=version_in.generation_parameters,
            source="backend_genblaze_refinement",
            based_on_version_id=latest_version.id if latest_version else None,
            input_assets=metadata_input_assets,
        )
        db.add(version)
        asset.updated_at = datetime.now(UTC)
        db.flush()
        add_asset_version_inputs(
            db=db,
            version=version,
            stored_inputs=version_inputs,
        )
        uploaded_artifact_key = upload_generated_artifact(
            storage=storage,
            campaign=campaign,
            asset=asset,
            version=version,
            result=generation_result,
        )
        upload_asset_version_sidecar(
            storage=storage,
            campaign=campaign,
            asset=asset,
            version=version,
        )
        db.commit()
    except HTTPException:
        db.rollback()
        delete_stored_inputs(storage=storage, stored_inputs=stored_inputs)
        delete_stored_artifact(storage=storage, storage_key=uploaded_artifact_key)
        raise
    except IntegrityError as exc:
        db.rollback()
        delete_stored_inputs(storage=storage, stored_inputs=stored_inputs)
        delete_stored_artifact(storage=storage, storage_key=uploaded_artifact_key)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Asset version number already exists",
        ) from exc
    except (StorageConfigurationError, BotoCoreError, ClientError) as exc:
        db.rollback()
        delete_stored_inputs(storage=storage, stored_inputs=stored_inputs)
        delete_stored_artifact(storage=storage, storage_key=uploaded_artifact_key)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Generated asset version was not saved because B2 storage failed",
        ) from exc

    return get_asset_or_404(asset.id, db)


@router.post(
    "/assets/{asset_id}/versions/{version_id}/artifact/ingest-generated",
    response_model=AssetVersionRead,
)
def ingest_generated_asset_version_artifact(
    asset_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    storage: B2StorageService = Depends(get_storage_service),
) -> AssetVersion:
    asset = get_asset_or_404(asset_id, db)
    campaign = ensure_campaign_exists(asset.campaign_id, db)
    version = get_asset_version_or_404(
        asset_id=asset_id,
        version_id=version_id,
        db=db,
    )
    generated_artifact = existing_generated_artifact_reference(
        asset=asset,
        version=version,
    )
    previous_artifact_key = version.artifact_storage_key
    uploaded_artifact_key: str | None = None

    try:
        uploaded_artifact_key = upload_generated_artifact_reference(
            storage=storage,
            campaign=campaign,
            asset=asset,
            version=version,
            artifact=generated_artifact,
            source="genblaze_ingest",
        )
        asset.updated_at = datetime.now(UTC)
        upload_asset_version_sidecar(
            storage=storage,
            campaign=campaign,
            asset=asset,
            version=version,
        )
        db.commit()
    except (StorageConfigurationError, BotoCoreError, ClientError) as exc:
        db.rollback()
        delete_stored_artifact(
            storage=storage,
            storage_key=uploaded_artifact_key,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Generated artifact was not ingested because B2 storage failed",
        ) from exc

    if (
        previous_artifact_key != uploaded_artifact_key
        and is_app_asset_version_artifact_key(
            asset=asset,
            version=version,
            storage_key=previous_artifact_key,
        )
    ):
        delete_stored_artifact(
            storage=storage,
            storage_key=previous_artifact_key,
        )

    db.refresh(version)

    return version


@router.post(
    "/assets/{asset_id}/versions/{version_id}/artifact",
    response_model=AssetVersionRead,
)
def upload_asset_version_artifact(
    asset_id: uuid.UUID,
    version_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    storage: B2StorageService = Depends(get_storage_service),
) -> AssetVersion:
    asset = get_asset_or_404(asset_id, db)
    campaign = ensure_campaign_exists(asset.campaign_id, db)
    version = get_asset_version_or_404(
        asset_id=asset_id,
        version_id=version_id,
        db=db,
    )

    try:
        artifact_filename = normalize_artifact_filename(file.filename or "")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    artifact_body = read_artifact_upload(file)
    artifact_content_type = file.content_type or "application/octet-stream"
    artifact_storage_key = build_asset_version_artifact_storage_key(
        campaign_id=asset.campaign_id,
        asset_id=asset.id,
        version_number=version.version_number,
        filename=artifact_filename,
    )
    previous_artifact_key = version.artifact_storage_key
    uploaded_artifact_key: str | None = None

    try:
        stored_artifact = storage.upload_bytes(
            key=artifact_storage_key,
            body=artifact_body,
            content_type=artifact_content_type,
            metadata={
                "campaign_id": str(campaign.id),
                "asset_id": str(asset.id),
                "version_id": str(version.id),
                "version_number": version.version_number,
                "content_kind": "asset-version-artifact",
                "filename": artifact_filename,
            },
        )
        uploaded_artifact_key = stored_artifact.key

        version.artifact_storage_key = stored_artifact.key
        version.artifact_filename = artifact_filename
        version.artifact_content_type = stored_artifact.content_type
        version.artifact_size_bytes = stored_artifact.size
        asset.updated_at = datetime.now(UTC)

        upload_asset_version_sidecar(
            storage=storage,
            campaign=campaign,
            asset=asset,
            version=version,
        )
        db.commit()
    except (StorageConfigurationError, BotoCoreError, ClientError) as exc:
        db.rollback()
        delete_stored_artifact(
            storage=storage,
            storage_key=uploaded_artifact_key,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Artifact was not uploaded because B2 storage failed",
        ) from exc

    if previous_artifact_key != uploaded_artifact_key:
        delete_stored_artifact(
            storage=storage,
            storage_key=previous_artifact_key,
        )

    db.refresh(version)

    return version


@router.get(
    "/assets/{asset_id}/versions/{version_id}/artifact/download-url",
    response_model=AssetVersionArtifactDownloadUrl,
)
def get_asset_version_artifact_download_url(
    asset_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    storage: B2StorageService = Depends(get_storage_service),
    expires_seconds: int = Query(default=3600, ge=60, le=604800),
) -> AssetVersionArtifactDownloadUrl:
    version = get_asset_version_or_404(
        asset_id=asset_id,
        version_id=version_id,
        db=db,
    )

    if version.artifact_storage_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset version artifact not found",
        )

    try:
        download_url = storage.generate_presigned_download_url(
            key=version.artifact_storage_key,
            expires_seconds=expires_seconds,
        )
    except (StorageConfigurationError, BotoCoreError, ClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Artifact download URL could not be created because B2 storage failed",
        ) from exc

    return AssetVersionArtifactDownloadUrl(
        asset_id=asset_id,
        version_id=version.id,
        artifact_storage_key=version.artifact_storage_key,
        artifact_filename=version.artifact_filename,
        artifact_content_type=version.artifact_content_type,
        artifact_size_bytes=version.artifact_size_bytes,
        download_url=download_url,
        expires_seconds=expires_seconds,
    )


@router.post(
    "/campaigns/{campaign_id}/assets",
    response_model=AssetRead,
    status_code=status.HTTP_201_CREATED,
)
def create_campaign_asset(
    campaign_id: uuid.UUID,
    asset_in: AssetCreate,
    db: Session = Depends(get_db),
    storage: B2StorageService = Depends(get_storage_service),
) -> Asset:
    campaign = ensure_campaign_exists(campaign_id, db)

    asset_data = asset_in.model_dump(exclude={"initial_version"})
    asset = Asset(campaign_id=campaign_id, **asset_data)

    try:
        db.add(asset)
        db.flush()

        if asset_in.initial_version is not None:
            version = make_asset_version(
                asset=asset,
                version_in=asset_in.initial_version,
            )
            db.add(version)
            db.flush()
            upload_asset_version_sidecar(
                storage=storage,
                campaign=campaign,
                asset=asset,
                version=version,
            )

        db.commit()
    except (StorageConfigurationError, BotoCoreError, ClientError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Asset was not created because B2 storage failed: {exc}",
        ) from exc

    db.refresh(asset)

    return get_asset_or_404(asset.id, db)


@router.get("/assets/{asset_id}", response_model=AssetRead)
def get_asset(
    asset_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> Asset:
    return get_asset_or_404(asset_id, db)


@router.patch("/assets/{asset_id}/status", response_model=AssetRead)
def update_asset_status(
    asset_id: uuid.UUID,
    status_in: AssetStatusUpdate,
    db: Session = Depends(get_db),
) -> Asset:
    asset = get_asset_or_404(asset_id, db)
    asset.status = status_in.status

    db.commit()
    db.refresh(asset)

    return get_asset_or_404(asset.id, db)


@router.get("/assets/{asset_id}/versions", response_model=list[AssetVersionRead])
def list_asset_versions(
    asset_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> list[AssetVersion]:
    get_asset_or_404(asset_id, db)

    statement = (
        select(AssetVersion)
        .options(selectinload(AssetVersion.inputs))
        .where(AssetVersion.asset_id == asset_id)
        .order_by(AssetVersion.version_number.desc())
    )
    return list(db.scalars(statement).all())


@router.get(
    "/assets/{asset_id}/versions/{version_id}/download-url",
    response_model=AssetVersionDownloadUrl,
)
def get_asset_version_download_url(
    asset_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    storage: B2StorageService = Depends(get_storage_service),
    expires_seconds: int = Query(default=3600, ge=60, le=604800),
) -> AssetVersionDownloadUrl:
    version = get_asset_version_or_404(
        asset_id=asset_id,
        version_id=version_id,
        db=db,
    )

    try:
        download_url = storage.generate_presigned_download_url(
            key=version.storage_key,
            expires_seconds=expires_seconds,
        )
    except (StorageConfigurationError, BotoCoreError, ClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Download URL could not be created because B2 storage failed",
        ) from exc

    return AssetVersionDownloadUrl(
        asset_id=asset_id,
        version_id=version.id,
        storage_key=version.storage_key,
        download_url=download_url,
        expires_seconds=expires_seconds,
    )


@router.post(
    "/assets/{asset_id}/versions",
    response_model=AssetVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_asset_version(
    asset_id: uuid.UUID,
    version_in: AssetVersionCreate,
    db: Session = Depends(get_db),
    storage: B2StorageService = Depends(get_storage_service),
) -> AssetVersion:
    asset = get_asset_or_404(asset_id, db)
    campaign = ensure_campaign_exists(asset.campaign_id, db)

    version = make_asset_version(asset=asset, version_in=version_in)

    try:
        db.add(version)
        db.flush()
        upload_asset_version_sidecar(
            storage=storage,
            campaign=campaign,
            asset=asset,
            version=version,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Asset version number already exists",
        ) from exc
    except (StorageConfigurationError, BotoCoreError, ClientError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Asset version was not created because B2 storage failed",
        ) from exc

    db.refresh(version)

    return version
