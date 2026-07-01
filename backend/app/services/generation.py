from __future__ import annotations

import inspect
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any
from urllib.parse import unquote, urlparse

from app.core.config import Settings, get_settings


class GenerationConfigurationError(RuntimeError):
    pass


class GenerationProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImageGenerationRequest:
    prompt: str
    model: str | None = None
    timeout_seconds: int | None = None
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GeneratedAsset:
    url: str | None
    storage_key: str | None
    sha256: str | None
    content_type: str | None
    size_bytes: int | None
    filename: str | None


@dataclass(frozen=True)
class GenerationResult:
    provider: str
    model: str
    prompt: str
    manifest_uri: str | None
    manifest_hash: str | None
    manifest_verified: bool | None
    assets: list[GeneratedAsset]
    generation_metadata: dict[str, Any]


def call_with_supported_kwargs(callable_object: Any, *args: Any, **kwargs: Any) -> Any:
    signature = inspect.signature(callable_object)
    accepts_var_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )

    if accepts_var_kwargs:
        return callable_object(*args, **kwargs)

    supported_kwargs = {
        key: value
        for key, value in kwargs.items()
        if key in signature.parameters
    }

    return callable_object(*args, **supported_kwargs)


def require_genblaze_imports() -> tuple[Any, ...]:
    try:
        from genblaze_core import KeyStrategy, Modality, ObjectStorageSink, Pipeline
        from genblaze_gmicloud import GMICloudImageProvider
        from genblaze_s3 import S3StorageBackend
    except ImportError as exc:
        raise GenerationConfigurationError(
            "Genblaze packages are not installed. Install backend requirements first."
        ) from exc

    return (
        KeyStrategy,
        Modality,
        ObjectStorageSink,
        Pipeline,
        GMICloudImageProvider,
        S3StorageBackend,
    )


def parse_b2_storage_key_from_url(url: str | None, bucket_name: str) -> str | None:
    if not url:
        return None

    parsed_url = urlparse(url)
    path_parts = [
        unquote(part)
        for part in parsed_url.path.split("/")
        if part
    ]

    if bucket_name in path_parts:
        bucket_index = path_parts.index(bucket_name)
        return "/".join(path_parts[bucket_index + 1 :]) or None

    if path_parts:
        return "/".join(path_parts)

    return None


def filename_from_storage_key(storage_key: str | None) -> str | None:
    if not storage_key:
        return None

    return storage_key.rsplit("/", maxsplit=1)[-1] or None


def extract_asset(asset: Any, bucket_name: str) -> GeneratedAsset:
    asset_url = getattr(asset, "url", None)
    storage_key = getattr(asset, "key", None) or parse_b2_storage_key_from_url(
        asset_url,
        bucket_name,
    )

    return GeneratedAsset(
        url=asset_url,
        storage_key=storage_key,
        sha256=getattr(asset, "sha256", None),
        content_type=getattr(asset, "mime_type", None)
        or getattr(asset, "content_type", None),
        size_bytes=getattr(asset, "size_bytes", None)
        or getattr(asset, "size", None),
        filename=getattr(asset, "filename", None)
        or filename_from_storage_key(storage_key),
    )


def coerce_pipeline_result(result: Any) -> tuple[Any, Any]:
    if isinstance(result, tuple) and len(result) == 2:
        return result

    return getattr(result, "run", None), getattr(result, "manifest", None)


class GenblazeGenerationService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _validate_settings(self) -> None:
        missing_settings = [
            name
            for name, value in {
                "GMI_API_KEY": self.settings.genblaze_gmi_api_key,
                "B2_BUCKET_NAME": self.settings.b2_bucket_name,
                "B2_APPLICATION_KEY_ID": self.settings.b2_application_key_id,
                "B2_APPLICATION_KEY": self.settings.b2_application_key,
            }.items()
            if not value
        ]

        if missing_settings:
            raise GenerationConfigurationError(
                "Missing required Genblaze settings: "
                + ", ".join(missing_settings)
            )

    def _configure_env(self) -> None:
        os.environ.setdefault("GMI_API_KEY", self.settings.genblaze_gmi_api_key)
        os.environ.setdefault("B2_BUCKET", self.settings.b2_bucket_name)
        os.environ.setdefault("B2_REGION", self.settings.b2_region_name)
        os.environ.setdefault("B2_KEY_ID", self.settings.b2_application_key_id)
        os.environ.setdefault("B2_APP_KEY", self.settings.b2_application_key)

    def _make_storage_sink(self) -> Any:
        (
            KeyStrategy,
            _Modality,
            ObjectStorageSink,
            _Pipeline,
            _GMICloudImageProvider,
            S3StorageBackend,
        ) = require_genblaze_imports()

        storage_backend = call_with_supported_kwargs(
            S3StorageBackend.for_backblaze,
            self.settings.b2_bucket_name,
            key_id=self.settings.b2_application_key_id,
            app_key=self.settings.b2_application_key,
            application_key=self.settings.b2_application_key,
            region=self.settings.b2_region_name,
            endpoint_url=self.settings.b2_endpoint_url,
        )

        return call_with_supported_kwargs(
            ObjectStorageSink,
            storage_backend,
            key_strategy=KeyStrategy.HIERARCHICAL,
            prefix=self.settings.genblaze_storage_prefix,
        )

    def generate_image(
        self,
        request: ImageGenerationRequest,
    ) -> GenerationResult:
        self._validate_settings()
        self._configure_env()

        (
            _KeyStrategy,
            Modality,
            _ObjectStorageSink,
            Pipeline,
            GMICloudImageProvider,
            _S3StorageBackend,
        ) = require_genblaze_imports()

        model = request.model or self.settings.genblaze_image_model
        timeout_seconds = (
            request.timeout_seconds or self.settings.genblaze_timeout_seconds
        )

        try:
            pipeline = Pipeline("sereneset-image-generation").step(
                GMICloudImageProvider(),
                model=model,
                prompt=request.prompt,
                modality=Modality.IMAGE,
                **request.parameters,
            )
            result = pipeline.run(
                sink=self._make_storage_sink(),
                timeout=timeout_seconds,
            )
        except Exception as exc:
            raise GenerationProviderError(
                f"Genblaze image generation failed: {exc}"
            ) from exc

        run, manifest = coerce_pipeline_result(result)
        steps = list(getattr(run, "steps", []) or [])
        assets = [
            extract_asset(asset, self.settings.b2_bucket_name)
            for step in steps
            for asset in (getattr(step, "assets", []) or [])
        ]
        manifest_verified: bool | None = None

        if manifest is not None and hasattr(manifest, "verify"):
            try:
                manifest_verified = bool(manifest.verify())
            except Exception:
                manifest_verified = None

        return GenerationResult(
            provider="gmicloud",
            model=model,
            prompt=request.prompt,
            manifest_uri=getattr(manifest, "manifest_uri", None),
            manifest_hash=getattr(manifest, "canonical_hash", None),
            manifest_verified=manifest_verified,
            assets=assets,
            generation_metadata={
                "genblaze": {
                    "provider": "gmicloud",
                    "model": model,
                    "manifest_uri": getattr(manifest, "manifest_uri", None),
                    "manifest_hash": getattr(manifest, "canonical_hash", None),
                    "manifest_verified": manifest_verified,
                    "asset_count": len(assets),
                }
            },
        )


@lru_cache
def get_generation_service() -> GenblazeGenerationService:
    return GenblazeGenerationService(get_settings())
