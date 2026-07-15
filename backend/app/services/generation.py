from __future__ import annotations

import inspect
import mimetypes
import os
from dataclasses import dataclass, field, replace
from enum import Enum
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

from app.core.config import Settings, get_settings


class GenerationConfigurationError(RuntimeError):
    pass


class GenerationProviderError(RuntimeError):
    pass


class GenerationInputError(ValueError):
    pass


GENBLAZE_EXTERNAL_INPUTS_PARAMETER = "external_inputs"
MAX_VIDEO_INPUT_SIZE_BYTES = 25 * 1024 * 1024
VIDEO_SOURCE_INPUT_ROLE = "source_creative"
GMI_VEO_DURATION_SECONDS = frozenset({4, 6, 8})
GMI_VEO_ASPECT_RATIOS = frozenset({"16:9", "9:16"})
GMI_VEO_RESOLUTIONS = frozenset({"720p", "1080p"})
GMI_VEO_WIRE_ALIASES = {
    "duration": "durationSeconds",
    "aspect_ratio": "aspectRatio",
}
ALLOWED_VIDEO_INPUT_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}
KNOWN_IMAGE_EXTENSION_CONTENT_TYPES = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
INPUT_ASSET_METADATA_KEYS = (
    "role",
    "storage_key",
    "filename",
    "content_type",
    "size_bytes",
    "sha256",
    "source",
    "storage_ownership",
    "brand_asset_id",
    "campaign_brand_asset_id",
    "brand_asset_type",
    "brand_asset_name",
    "usage_guidance",
)


@dataclass(frozen=True)
class ImageGenerationRequest:
    prompt: str
    model: str | None = None
    timeout_seconds: int | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    input_assets: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class VideoGenerationRequest:
    prompt: str
    model: str | None = None
    timeout_seconds: int | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    input_assets: list[dict[str, Any]] = field(default_factory=list)
    context_assets: list[dict[str, Any]] = field(default_factory=list)


class VideoInputMode(str, Enum):
    text_to_video = "text_to_video"
    image_to_video = "image_to_video"


@dataclass(frozen=True)
class VideoInputPlan:
    mode: VideoInputMode
    provider_input_assets: list[dict[str, Any]]
    provenance_input_assets: list[dict[str, Any]]


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
    provider_job_id: str | None
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
        from genblaze_core import (
            Asset as GenblazeAsset,
            KeyStrategy,
            Modality,
            ObjectStorageSink,
            Pipeline,
        )
        from genblaze_gmicloud import GMICloudImageProvider
        from genblaze_s3 import S3StorageBackend
    except ImportError as exc:
        raise GenerationConfigurationError(
            "Genblaze packages are not installed. Install backend requirements first."
        ) from exc

    return (
        GenblazeAsset,
        KeyStrategy,
        Modality,
        ObjectStorageSink,
        Pipeline,
        GMICloudImageProvider,
        S3StorageBackend,
    )


def require_genblaze_video_imports() -> tuple[Any, ...]:
    try:
        from genblaze_core import Asset as GenblazeAsset
        from genblaze_core import Modality, Pipeline
        from genblaze_core.providers import RetryPolicy
        from genblaze_gmicloud import GMICloudVideoProvider
    except ImportError as exc:
        raise GenerationConfigurationError(
            "Genblaze video packages are not installed. Install backend "
            "requirements first."
        ) from exc

    return (
        GenblazeAsset,
        Modality,
        Pipeline,
        GMICloudVideoProvider,
        RetryPolicy,
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


def is_video_asset(asset: GeneratedAsset) -> bool:
    content_type = (asset.content_type or "").split(";", maxsplit=1)[0].lower()
    if content_type.startswith("video/"):
        return True

    filename = (asset.filename or asset.storage_key or asset.url or "").lower()
    return filename.endswith((".mp4", ".mov", ".webm", ".m4v"))


def coerce_pipeline_result(result: Any) -> tuple[Any, Any]:
    if isinstance(result, tuple) and len(result) == 2:
        return result

    return getattr(result, "run", None), getattr(result, "manifest", None)


def verify_manifest(manifest: Any) -> bool | None:
    if manifest is None or not hasattr(manifest, "verify"):
        return None

    try:
        return bool(manifest.verify())
    except Exception:
        return None


def extract_provider_job_id(steps: list[Any]) -> str | None:
    for step in reversed(steps):
        provider_payload = getattr(step, "provider_payload", None)
        if not isinstance(provider_payload, dict):
            continue

        gmicloud_payload = provider_payload.get("gmicloud")
        if not isinstance(gmicloud_payload, dict):
            continue

        request_id = gmicloud_payload.get("request_id")
        if request_id is not None and str(request_id).strip():
            return str(request_id)

    return None


def extract_generation_step_error(steps: list[Any]) -> str | None:
    for step in reversed(steps):
        for attribute in ("error", "error_message"):
            value = getattr(step, attribute, None)
            if value is not None and str(value).strip():
                return str(value).strip()

    return None


def serialize_input_asset(input_asset: dict[str, Any]) -> dict[str, Any]:
    return {
        key: input_asset[key]
        for key in INPUT_ASSET_METADATA_KEYS
        if input_asset.get(key) is not None
    }


def optional_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value

    return None


def optional_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value

    return None


def infer_asset_media_type(
    *,
    content_type: str | None,
    filename: str | None,
    url: str | None,
) -> str:
    normalized_content_type = (content_type or "").split(";")[0].strip().lower()
    media_path = unquote(urlparse(filename or url or "").path).replace("\\", "/")
    known_content_type = KNOWN_IMAGE_EXTENSION_CONTENT_TYPES.get(
        PurePosixPath(media_path).suffix.lower()
    )
    guessed_content_type, _encoding = mimetypes.guess_type(media_path)
    inferred_content_type = known_content_type or guessed_content_type

    if (
        inferred_content_type
        and (
            not normalized_content_type
            or normalized_content_type == "application/octet-stream"
        )
    ):
        return inferred_content_type

    return normalized_content_type or "image/png"


def build_external_input_assets(
    *,
    input_assets: list[dict[str, Any]],
    genblaze_asset_class: Any,
) -> list[Any]:
    external_inputs: list[Any] = []
    missing_url_filenames: list[str] = []

    for input_asset in input_assets:
        url = optional_string(input_asset.get("url"))
        filename = optional_string(input_asset.get("filename")) or "input image"
        if url is None:
            missing_url_filenames.append(filename)
            continue

        external_inputs.append(
            genblaze_asset_class(
                url=url,
                media_type=infer_asset_media_type(
                    content_type=optional_string(input_asset.get("content_type")),
                    filename=filename,
                    url=url,
                ),
                sha256=optional_string(input_asset.get("sha256")),
                size_bytes=optional_int(input_asset.get("size_bytes")),
                metadata=serialize_input_asset(input_asset),
            )
        )

    if missing_url_filenames:
        joined_filenames = ", ".join(missing_url_filenames)
        raise GenerationProviderError(
            "Generation input images were uploaded but no downloadable B2 URL "
            f"was prepared for: {joined_filenames}"
        )

    return external_inputs


def build_pipeline_parameters(
    *,
    parameters: dict[str, Any],
    input_assets: list[dict[str, Any]],
) -> tuple[dict[str, Any], str | None]:
    pipeline_parameters = dict(parameters)
    pipeline_parameters.pop(GENBLAZE_EXTERNAL_INPUTS_PARAMETER, None)

    if not input_assets:
        return pipeline_parameters, None

    return pipeline_parameters, GENBLAZE_EXTERNAL_INPUTS_PARAMETER


def is_gmicloud_veo_model(model: str) -> bool:
    return model.casefold().startswith("veo")


def validate_video_generation_parameters(
    *,
    model: str,
    duration_seconds: int,
    aspect_ratio: str,
    resolution: str,
) -> None:
    if not is_gmicloud_veo_model(model):
        return

    if duration_seconds not in GMI_VEO_DURATION_SECONDS:
        raise GenerationInputError(
            f"Video model '{model}' supports durations of 4, 6, or 8 seconds"
        )
    if aspect_ratio not in GMI_VEO_ASPECT_RATIOS:
        raise GenerationInputError(
            f"Video model '{model}' supports aspect ratios 16:9 or 9:16"
        )
    if resolution not in GMI_VEO_RESOLUTIONS:
        raise GenerationInputError(
            f"Video model '{model}' supports resolutions 720p or 1080p"
        )


def coerce_gmi_duration_seconds(value: Any) -> str:
    return str(int(value))


def build_gmicloud_video_models(
    provider_class: Any,
    *,
    model: str,
) -> Any | None:
    if not is_gmicloud_veo_model(model):
        return None

    models_default = getattr(provider_class, "models_default", None)
    if not callable(models_default):
        return None

    models = models_default().fork()
    spec = models.get(model)
    wire_parameter_names = frozenset(GMI_VEO_WIRE_ALIASES.values())
    allowlist = (
        spec.param_allowlist | wire_parameter_names
        if spec.param_allowlist is not None
        else None
    )
    models.register(
        replace(
            spec,
            model_id=model,
            param_aliases={
                **spec.param_aliases,
                **GMI_VEO_WIRE_ALIASES,
            },
            param_coercers={
                **spec.param_coercers,
                "durationSeconds": coerce_gmi_duration_seconds,
            },
            param_required=(
                spec.param_required | frozenset({"durationSeconds"})
            ),
            param_allowlist=allowlist,
        )
    )
    return models


def video_model_input_requirement(model: str) -> str:
    normalized_model = model.casefold()

    if "transition" in normalized_model:
        return "unsupported"

    if "image2video" in normalized_model or any(
        marker in normalized_model for marker in ("-i2v", "-r2v")
    ):
        return "required"

    if "text2video" in normalized_model or "-t2v" in normalized_model:
        return "forbidden"

    return "optional"


def validate_video_input_assets(
    *,
    model: str,
    input_assets: list[dict[str, Any]],
    require_download_url: bool = True,
) -> VideoInputMode:
    if len(input_assets) > 1:
        raise GenerationInputError(
            "Video generation accepts at most one source image"
        )

    input_requirement = video_model_input_requirement(model)
    if input_requirement == "unsupported":
        raise GenerationInputError(
            f"Video model '{model}' requires an unsupported multi-image flow"
        )

    if not input_assets:
        if input_requirement == "required":
            raise GenerationInputError(
                f"Video model '{model}' requires one source image"
            )

        return VideoInputMode.text_to_video

    if input_requirement == "forbidden":
        raise GenerationInputError(
            f"Video model '{model}' only supports text-to-video generation"
        )

    input_asset = input_assets[0]
    role = optional_string(input_asset.get("role"))
    if role != VIDEO_SOURCE_INPUT_ROLE:
        raise GenerationInputError(
            "The video source image must use role 'source_creative'"
        )

    url = optional_string(input_asset.get("url"))
    if require_download_url and (
        url is None or urlparse(url).scheme.lower() != "https"
    ):
        raise GenerationInputError(
            "The video source image must have a downloadable HTTPS URL"
        )

    content_type = infer_asset_media_type(
        content_type=optional_string(input_asset.get("content_type")),
        filename=optional_string(input_asset.get("filename")),
        url=url,
    )
    if content_type not in ALLOWED_VIDEO_INPUT_CONTENT_TYPES:
        supported_types = ", ".join(sorted(ALLOWED_VIDEO_INPUT_CONTENT_TYPES))
        raise GenerationInputError(
            "The video source image type is not supported. "
            f"Use one of: {supported_types}"
        )

    size_bytes = optional_int(input_asset.get("size_bytes"))
    if size_bytes is None or size_bytes <= 0:
        raise GenerationInputError(
            "The video source image must have a positive size"
        )

    if size_bytes > MAX_VIDEO_INPUT_SIZE_BYTES:
        raise GenerationInputError(
            "The video source image must be 25 MB or smaller"
        )

    return VideoInputMode.image_to_video


def build_video_input_plan(
    *,
    model: str,
    source_input_assets: list[dict[str, Any]],
    context_assets: list[dict[str, Any]],
) -> VideoInputPlan:
    mode = validate_video_input_assets(
        model=model,
        input_assets=source_input_assets,
    )
    return VideoInputPlan(
        mode=mode,
        provider_input_assets=list(source_input_assets),
        provenance_input_assets=[*source_input_assets, *context_assets],
    )


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
            _GenblazeAsset,
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
            GenblazeAsset,
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
        pipeline_parameters, input_assets_parameter = build_pipeline_parameters(
            parameters=request.parameters,
            input_assets=request.input_assets,
        )
        external_inputs = build_external_input_assets(
            input_assets=request.input_assets,
            genblaze_asset_class=GenblazeAsset,
        )

        try:
            pipeline = Pipeline("sereneset-image-generation").step(
                GMICloudImageProvider(),
                model=model,
                prompt=request.prompt,
                modality=Modality.IMAGE,
                external_inputs=external_inputs or None,
                **pipeline_parameters,
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
        manifest_verified = verify_manifest(manifest)
        provider_job_id = extract_provider_job_id(steps)

        return GenerationResult(
            provider="gmicloud",
            model=model,
            prompt=request.prompt,
            manifest_uri=getattr(manifest, "manifest_uri", None),
            manifest_hash=getattr(manifest, "canonical_hash", None),
            manifest_verified=manifest_verified,
            provider_job_id=provider_job_id,
            assets=assets,
            generation_metadata={
                "genblaze": {
                    "provider": "gmicloud",
                    "model": model,
                    "manifest_uri": getattr(manifest, "manifest_uri", None),
                    "manifest_hash": getattr(manifest, "canonical_hash", None),
                    "manifest_verified": manifest_verified,
                    "provider_job_id": provider_job_id,
                    "asset_count": len(assets),
                    "input_asset_count": len(request.input_assets),
                    "external_input_count": len(external_inputs),
                    "input_assets_parameter": input_assets_parameter,
                }
            },
        )

    def generate_video(
        self,
        request: VideoGenerationRequest,
    ) -> GenerationResult:
        self._validate_settings()
        self._configure_env()

        (
            GenblazeAsset,
            Modality,
            Pipeline,
            GMICloudVideoProvider,
            RetryPolicy,
        ) = require_genblaze_video_imports()

        model = request.model or self.settings.genblaze_video_model
        timeout_seconds = (
            request.timeout_seconds
            or self.settings.genblaze_video_timeout_seconds
        )
        input_plan = build_video_input_plan(
            model=model,
            source_input_assets=request.input_assets,
            context_assets=request.context_assets,
        )
        pipeline_parameters, input_assets_parameter = build_pipeline_parameters(
            parameters=request.parameters,
            input_assets=input_plan.provider_input_assets,
        )
        external_inputs = build_external_input_assets(
            input_assets=input_plan.provider_input_assets,
            genblaze_asset_class=GenblazeAsset,
        )
        video_provider_models = build_gmicloud_video_models(
            GMICloudVideoProvider,
            model=model,
        )
        video_provider = call_with_supported_kwargs(
            GMICloudVideoProvider,
            retry_policy=RetryPolicy.conservative(),
            models=video_provider_models,
        )

        try:
            pipeline = Pipeline("sereneset-video-generation").step(
                video_provider,
                model=model,
                prompt=request.prompt,
                modality=Modality.VIDEO,
                external_inputs=external_inputs or None,
                **pipeline_parameters,
            )
            result = pipeline.run(
                sink=self._make_storage_sink(),
                timeout=timeout_seconds,
            )
        except Exception as exc:
            raise GenerationProviderError(
                f"Genblaze video generation failed: {exc}"
            ) from exc

        run, manifest = coerce_pipeline_result(result)
        steps = list(getattr(run, "steps", []) or [])
        generated_assets = [
            extract_asset(asset, self.settings.b2_bucket_name)
            for step in steps
            for asset in (getattr(step, "assets", []) or [])
        ]
        assets = [asset for asset in generated_assets if is_video_asset(asset)]
        if not assets:
            step_error = extract_generation_step_error(steps)
            if step_error:
                raise GenerationProviderError(
                    f"Genblaze video generation failed: {step_error}"
                )
            raise GenerationProviderError(
                "Genblaze video generation did not return a video artifact"
            )

        manifest_verified = verify_manifest(manifest)
        provider_job_id = extract_provider_job_id(steps)

        return GenerationResult(
            provider="gmicloud",
            model=model,
            prompt=request.prompt,
            manifest_uri=getattr(manifest, "manifest_uri", None),
            manifest_hash=getattr(manifest, "canonical_hash", None),
            manifest_verified=manifest_verified,
            provider_job_id=provider_job_id,
            assets=assets,
            generation_metadata={
                "genblaze": {
                    "provider": "gmicloud",
                    "model": model,
                    "modality": "video",
                    "input_mode": input_plan.mode.value,
                    "manifest_uri": getattr(manifest, "manifest_uri", None),
                    "manifest_hash": getattr(manifest, "canonical_hash", None),
                    "manifest_verified": manifest_verified,
                    "provider_job_id": provider_job_id,
                    "asset_count": len(assets),
                    "input_asset_count": len(input_plan.provider_input_assets),
                    "context_asset_count": len(request.context_assets),
                    "provenance_asset_count": len(
                        input_plan.provenance_input_assets
                    ),
                    "external_input_count": len(external_inputs),
                    "input_assets_parameter": input_assets_parameter,
                },
                "input_assets": [
                    serialize_input_asset(input_asset)
                    for input_asset in input_plan.provenance_input_assets
                ],
            },
        )


@lru_cache
def get_generation_service() -> GenblazeGenerationService:
    return GenblazeGenerationService(get_settings())
