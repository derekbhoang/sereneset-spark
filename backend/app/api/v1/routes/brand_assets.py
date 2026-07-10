import hashlib
import uuid
from pathlib import PurePosixPath

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.brand_asset import BrandAsset, BrandAssetType
from app.schemas.brand_asset import (
    BrandAssetCreate,
    BrandAssetDownloadUrl,
    BrandAssetRead,
    BrandAssetUpdate,
)
from app.services.storage import (
    B2StorageService,
    StorageConfigurationError,
    build_brand_asset_storage_key,
    get_storage_service,
    normalize_artifact_filename,
)


router = APIRouter(prefix="/brand-assets", tags=["brand-assets"])
MAX_BRAND_ASSET_SIZE_BYTES = 25 * 1024 * 1024
BRAND_ASSET_CONTENT_TYPES_BY_SUFFIX = {
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".md": "text/markdown",
    ".otf": "font/otf",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    ".svg": "image/svg+xml",
    ".ttf": "font/ttf",
    ".txt": "text/plain",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


def get_brand_asset_or_404(
    brand_asset_id: uuid.UUID,
    db: Session,
) -> BrandAsset:
    brand_asset = db.get(BrandAsset, brand_asset_id)
    if brand_asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Brand asset not found",
        )

    return brand_asset


def parse_brand_asset_payload(payload: str) -> BrandAssetCreate:
    try:
        return BrandAssetCreate.model_validate_json(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc


def normalize_brand_asset_filename(file: UploadFile) -> str:
    try:
        return normalize_artifact_filename(file.filename or "")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc).replace("Artifact", "Brand asset"),
        ) from exc


def normalize_brand_asset_content_type(filename: str) -> str:
    suffix = PurePosixPath(filename).suffix.lower()
    content_type = BRAND_ASSET_CONTENT_TYPES_BY_SUFFIX.get(suffix)

    if content_type is None:
        supported_extensions = ", ".join(
            sorted(BRAND_ASSET_CONTENT_TYPES_BY_SUFFIX)
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Brand asset file type is not supported. "
                f"Use one of: {supported_extensions}"
            ),
        )

    return content_type


def read_brand_asset_upload(file: UploadFile) -> bytes:
    file.file.seek(0)
    body = file.file.read(MAX_BRAND_ASSET_SIZE_BYTES + 1)
    file.file.seek(0)

    if not body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Brand asset file must not be empty",
        )

    if len(body) > MAX_BRAND_ASSET_SIZE_BYTES:
        max_megabytes = MAX_BRAND_ASSET_SIZE_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Brand asset file must be {max_megabytes} MB or smaller",
        )

    return body


def delete_uploaded_object_safely(
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


@router.get("", response_model=list[BrandAssetRead])
def list_brand_assets(
    db: Session = Depends(get_db),
    asset_type: BrandAssetType | None = Query(default=None),
    is_active: bool = Query(default=True),
    search: str | None = Query(default=None, min_length=1, max_length=160),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[BrandAsset]:
    statement = select(BrandAsset).where(BrandAsset.is_active.is_(is_active))

    if asset_type is not None:
        statement = statement.where(BrandAsset.asset_type == asset_type)

    if search is not None:
        statement = statement.where(BrandAsset.name.ilike(f"%{search.strip()}%"))

    statement = (
        statement.order_by(BrandAsset.created_at.desc()).offset(offset).limit(limit)
    )
    return list(db.scalars(statement).all())


@router.post("", response_model=BrandAssetRead, status_code=status.HTTP_201_CREATED)
def create_brand_asset(
    payload: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    storage: B2StorageService = Depends(get_storage_service),
) -> BrandAsset:
    brand_asset_in = parse_brand_asset_payload(payload)
    filename = normalize_brand_asset_filename(file)
    content_type = normalize_brand_asset_content_type(filename)
    body = read_brand_asset_upload(file)
    brand_asset_id = uuid.uuid4()
    storage_key = build_brand_asset_storage_key(
        brand_asset_id=brand_asset_id,
        filename=filename,
    )
    sha256 = hashlib.sha256(body).hexdigest()

    try:
        stored_object = storage.upload_bytes(
            key=storage_key,
            body=body,
            content_type=content_type,
            metadata={
                "brand_asset_id": str(brand_asset_id),
                "asset_type": brand_asset_in.asset_type.value,
                "content_kind": "brand-asset-original",
                "filename": filename,
                "sha256": sha256,
            },
        )
    except (StorageConfigurationError, BotoCoreError, ClientError) as exc:
        delete_uploaded_object_safely(
            storage=storage,
            storage_key=storage_key,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Brand asset was not uploaded because B2 storage failed",
        ) from exc

    brand_asset = BrandAsset(
        id=brand_asset_id,
        **brand_asset_in.model_dump(),
        storage_key=stored_object.key,
        filename=filename,
        content_type=stored_object.content_type,
        size_bytes=stored_object.size,
        sha256=sha256,
        is_active=True,
    )
    db.add(brand_asset)

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        delete_uploaded_object_safely(
            storage=storage,
            storage_key=stored_object.key,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Brand asset metadata could not be saved",
        ) from exc

    db.refresh(brand_asset)
    return brand_asset


@router.get("/{brand_asset_id}", response_model=BrandAssetRead)
def get_brand_asset(
    brand_asset_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> BrandAsset:
    return get_brand_asset_or_404(brand_asset_id, db)


@router.patch("/{brand_asset_id}", response_model=BrandAssetRead)
def update_brand_asset(
    brand_asset_id: uuid.UUID,
    brand_asset_in: BrandAssetUpdate,
    db: Session = Depends(get_db),
) -> BrandAsset:
    brand_asset = get_brand_asset_or_404(brand_asset_id, db)

    for field, value in brand_asset_in.model_dump(exclude_unset=True).items():
        setattr(brand_asset, field, value)

    db.commit()
    db.refresh(brand_asset)
    return brand_asset


@router.delete(
    "/{brand_asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive a brand asset",
)
def archive_brand_asset(
    brand_asset_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> Response:
    brand_asset = get_brand_asset_or_404(brand_asset_id, db)
    brand_asset.is_active = False
    db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{brand_asset_id}/download-url",
    response_model=BrandAssetDownloadUrl,
)
def get_brand_asset_download_url(
    brand_asset_id: uuid.UUID,
    db: Session = Depends(get_db),
    storage: B2StorageService = Depends(get_storage_service),
    expires_seconds: int = Query(default=3600, ge=60, le=604800),
) -> BrandAssetDownloadUrl:
    brand_asset = get_brand_asset_or_404(brand_asset_id, db)

    try:
        download_url = storage.generate_presigned_download_url(
            key=brand_asset.storage_key,
            expires_seconds=expires_seconds,
        )
    except (StorageConfigurationError, BotoCoreError, ClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Brand asset download URL could not be created because "
                "B2 storage failed"
            ),
        ) from exc

    return BrandAssetDownloadUrl(
        brand_asset_id=brand_asset.id,
        storage_key=brand_asset.storage_key,
        filename=brand_asset.filename,
        content_type=brand_asset.content_type,
        size_bytes=brand_asset.size_bytes,
        download_url=download_url,
        expires_seconds=expires_seconds,
    )
