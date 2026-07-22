from __future__ import annotations

import inspect
import mimetypes
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

from app.core.config import Settings, get_settings
from app.models.asset import AssetInputMediaKind
from app.services.input_provenance import infer_input_media_kind
from app.services.video_model_capabilities import (
    VideoModelCapability,
    VideoModelInputRequirement,
    VideoSourceMediaKind,
    get_video_model_capability,
)


class GenerationConfigurationError(RuntimeError):
    pass


class GenerationProviderError(RuntimeError):
    pass


class GenerationInputError(ValueError):
    pass


GENBLAZE_EXTERNAL_INPUTS_PARAMETER = "external_inputs"
GENBLAZE_PIPELINE_RESERVED_PARAMETERS = frozenset(
    {
        "expected_duration_sec",
        GENBLAZE_EXTERNAL_INPUTS_PARAMETER,
        "fallback_models",
        "input_from",
        "model",
        "modality",
        "prompt",
        "provider",
        "step_type",
    }
)
MAX_VIDEO_SOURCE_IMAGE_SIZE_BYTES = 25 * 1024 * 1024
MAX_VIDEO_SOURCE_VIDEO_SIZE_BYTES = 100 * 1024 * 1024
VIDEO_SOURCE_INPUT_ROLE = "source_creative"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_VIDEO_SOURCE_IMAGE_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}
ALLOWED_VIDEO_SOURCE_VIDEO_CONTENT_TYPES = {
    "video/mp4",
}
KNOWN_MEDIA_EXTENSION_CONTENT_TYPES = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".mp4": "video/mp4",
    ".png": "image/png",
    ".webp": "image/webp",
}
INPUT_ASSET_METADATA_KEYS = (
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
    video_to_video = "video_to_video"


@dataclass(frozen=True)
class VideoInputPlan:
    mode: VideoInputMode
    provider_input_assets: list[dict[str, Any]]
    provenance_input_assets: list[dict[str, Any]]


@dataclass(frozen=True)
class ValidatedVideoSourceInput:
    filename: str
    content_type: str
    media_kind: VideoSourceMediaKind
    size_bytes: int


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
    known_content_type = KNOWN_MEDIA_EXTENSION_CONTENT_TYPES.get(
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
            "Generation input media was stored but no downloadable B2 URL "
            f"was prepared for: {joined_filenames}"
        )

    return external_inputs


def build_pipeline_parameters(
    *,
    parameters: dict[str, Any],
    input_assets: list[dict[str, Any]],
) -> tuple[dict[str, Any], str | None]:
    pipeline_parameters = dict(parameters)
    for reserved_parameter in GENBLAZE_PIPELINE_RESERVED_PARAMETERS:
        pipeline_parameters.pop(reserved_parameter, None)

    if not input_assets:
        return pipeline_parameters, None

    return pipeline_parameters, GENBLAZE_EXTERNAL_INPUTS_PARAMETER


def build_video_pipeline_parameters(
    *,
    parameters: dict[str, Any],
    input_assets: list[dict[str, Any]],
    capability: VideoModelCapability,
) -> tuple[dict[str, Any], str | None]:
    pipeline_parameters, input_assets_parameter = build_pipeline_parameters(
        parameters=parameters,
        input_assets=input_assets,
    )

    source_parameter = capability.provider_source_parameter
    if source_parameter is not None:
        pipeline_parameters.pop(source_parameter, None)

    allowed_parameters = capability.provider_allowed_parameters
    if allowed_parameters is not None:
        canonical_allowed_parameters = set(allowed_parameters)
        canonical_allowed_parameters.update(
            canonical
            for canonical, native in capability.provider_parameter_aliases
            if native in allowed_parameters
        )
        pipeline_parameters = {
            key: value
            for key, value in pipeline_parameters.items()
            if key in canonical_allowed_parameters
        }

    return pipeline_parameters, input_assets_parameter


def require_video_model_capability(model: str) -> VideoModelCapability:
    capability = get_video_model_capability(model)
    if capability is None:
        raise GenerationInputError(
            f"Video model '{model}' is not registered for backend generation"
        )

    return capability


def format_supported_values(values: tuple[object, ...]) -> str:
    rendered_values = [str(value) for value in values]
    if len(rendered_values) == 1:
        return rendered_values[0]
    if len(rendered_values) == 2:
        return " or ".join(rendered_values)

    return ", ".join(rendered_values[:-1]) + f", or {rendered_values[-1]}"


def validate_video_generation_parameters(
    *,
    model: str,
    duration_seconds: int,
    aspect_ratio: str,
    resolution: str,
) -> None:
    capability = require_video_model_capability(model)

    if (
        capability.allowed_duration_seconds is not None
        and duration_seconds not in capability.allowed_duration_seconds
    ):
        raise GenerationInputError(
            f"Video model '{model}' supports durations of "
            f"{format_supported_values(capability.allowed_duration_seconds)} seconds"
        )
    if (
        capability.allowed_aspect_ratios is not None
        and aspect_ratio not in capability.allowed_aspect_ratios
    ):
        raise GenerationInputError(
            f"Video model '{model}' supports aspect ratios "
            f"{format_supported_values(capability.allowed_aspect_ratios)}"
        )
    if (
        capability.allowed_resolutions is not None
        and resolution not in capability.allowed_resolutions
    ):
        raise GenerationInputError(
            f"Video model '{model}' supports resolutions "
            f"{format_supported_values(capability.allowed_resolutions)}"
        )


def coerce_gmi_duration_seconds(value: Any) -> str:
    return str(int(value))


def build_provider_source_input_mapping(
    capability: VideoModelCapability,
) -> Callable[[Sequence[Any]], dict[str, str]] | None:
    source_parameter = capability.provider_source_parameter
    if (
        source_parameter is None
        or not capability.provider_source_routing_implemented
    ):
        return None

    accepted_media_prefixes = tuple(
        f"{media_kind.value}/"
        for media_kind in capability.accepted_source_media_kinds
    )

    def route_source_input(inputs: Sequence[Any]) -> dict[str, str]:
        for input_asset in inputs:
            media_type = getattr(input_asset, "media_type", "") or ""
            if not str(media_type).casefold().startswith(accepted_media_prefixes):
                continue

            url = getattr(input_asset, "url", None)
            if isinstance(url, str) and url.strip():
                return {source_parameter: url}

        return {}

    return route_source_input


def build_gmicloud_video_models(
    provider_class: Any,
    *,
    model: str,
) -> Any | None:
    capability = get_video_model_capability(model)
    if capability is None or capability.provider != "gmicloud":
        return None

    parameter_aliases = dict(capability.provider_parameter_aliases)
    if not (
        parameter_aliases
        or capability.provider_required_parameters
        or capability.provider_integer_string_parameters
        or capability.provider_allowed_parameters is not None
        or capability.provider_source_routing_implemented
    ):
        return None

    models_default = getattr(provider_class, "models_default", None)
    if not callable(models_default):
        return None

    models = models_default().fork()
    spec = models.get(model)
    provider_parameter_names = (
        frozenset(parameter_aliases.values())
        | capability.provider_required_parameters
        | capability.provider_integer_string_parameters
    )
    if capability.provider_source_parameter is not None:
        provider_parameter_names |= frozenset(
            {capability.provider_source_parameter}
        )
    allowlist = capability.provider_allowed_parameters
    if allowlist is None and spec.param_allowlist is not None:
        allowlist = spec.param_allowlist | provider_parameter_names
    source_input_mapping = build_provider_source_input_mapping(capability)
    models.register(
        replace(
            spec,
            model_id=model,
            param_aliases={
                **spec.param_aliases,
                **parameter_aliases,
            },
            param_coercers={
                **spec.param_coercers,
                **{
                    parameter: coerce_gmi_duration_seconds
                    for parameter in (
                        capability.provider_integer_string_parameters
                    )
                },
            },
            param_required=(
                spec.param_required | capability.provider_required_parameters
            ),
            param_allowlist=allowlist,
            input_mapping=source_input_mapping or spec.input_mapping,
        )
    )
    return models


def video_model_input_requirement(model: str) -> VideoModelInputRequirement:
    return require_video_model_capability(model).input_requirement


def video_source_media_kind(content_type: str) -> VideoSourceMediaKind:
    media_kind = infer_input_media_kind(content_type)
    if media_kind == AssetInputMediaKind.image:
        return VideoSourceMediaKind.image
    if media_kind == AssetInputMediaKind.video:
        return VideoSourceMediaKind.video

    raise GenerationInputError(
        "The video generation source must be an image or video file"
    )


def video_to_video_routing_enabled(
    model: str,
    *,
    settings: Settings | None = None,
) -> bool:
    if settings is None or not settings.genblaze_video_to_video_enabled:
        return False

    capability = get_video_model_capability(model)
    if capability is None:
        return False

    normalized_model = model.strip().casefold()
    return (
        normalized_model == settings.genblaze_video_edit_model.casefold()
        and capability.input_requirement
        == VideoModelInputRequirement.video_required
        and capability.provider_source_parameter is not None
        and capability.provider_source_routing_implemented
    )


def format_size_limit(size_bytes: int) -> str:
    mebibyte = 1024 * 1024
    if size_bytes % mebibyte == 0:
        return f"{size_bytes // mebibyte} MB"

    return f"{size_bytes} bytes"


def validate_video_source_metadata(
    *,
    input_asset: dict[str, Any],
    require_download_url: bool,
) -> ValidatedVideoSourceInput:
    role = optional_string(input_asset.get("role"))
    if role != VIDEO_SOURCE_INPUT_ROLE:
        raise GenerationInputError(
            "The video generation source must use role 'source_creative'"
        )

    url = optional_string(input_asset.get("url"))
    if require_download_url:
        if url is None or urlparse(url).scheme.lower() != "https":
            raise GenerationInputError(
                "The video generation source must have a downloadable HTTPS URL"
            )
    elif optional_string(input_asset.get("storage_key")) is None:
        raise GenerationInputError(
            "The video generation source must have a B2 storage key"
        )

    filename = optional_string(input_asset.get("filename"))
    if filename is None:
        raise GenerationInputError(
            "The video generation source must have a filename"
        )

    declared_content_type = optional_string(input_asset.get("content_type"))
    normalized_content_type = (
        declared_content_type.split(";", maxsplit=1)[0].strip().lower()
        if declared_content_type is not None
        else None
    )
    filename_content_type = KNOWN_MEDIA_EXTENSION_CONTENT_TYPES.get(
        PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    )
    if (
        normalized_content_type
        and normalized_content_type != "application/octet-stream"
        and filename_content_type is not None
        and normalized_content_type != filename_content_type
    ):
        raise GenerationInputError(
            "The video generation source filename and content type do not match"
        )

    content_type = infer_asset_media_type(
        content_type=declared_content_type,
        filename=filename,
        url=url,
    )
    media_kind = video_source_media_kind(content_type)
    declared_media_kind = optional_string(input_asset.get("media_kind"))
    if (
        declared_media_kind is not None
        and declared_media_kind.casefold() != media_kind.value
    ):
        raise GenerationInputError(
            "The video generation source media kind does not match its content type"
        )

    size_bytes = optional_int(input_asset.get("size_bytes"))
    if size_bytes is None or size_bytes <= 0:
        raise GenerationInputError(
            "The video generation source must have a positive size"
        )

    sha256 = optional_string(input_asset.get("sha256"))
    if sha256 is not None and SHA256_PATTERN.fullmatch(sha256) is None:
        raise GenerationInputError(
            "The video generation source SHA-256 checksum is invalid"
        )

    if (
        media_kind == VideoSourceMediaKind.video
        and PurePosixPath(filename).suffix.casefold() != ".mp4"
    ):
        raise GenerationInputError(
            "The source video filename must use the .mp4 extension"
        )

    return ValidatedVideoSourceInput(
        filename=filename,
        content_type=content_type,
        media_kind=media_kind,
        size_bytes=size_bytes,
    )


def validate_video_input_assets(
    *,
    model: str,
    input_assets: list[dict[str, Any]],
    require_download_url: bool = True,
    settings: Settings | None = None,
) -> VideoInputMode:
    if len(input_assets) > 1:
        raise GenerationInputError("Video generation accepts at most one source file")

    capability = require_video_model_capability(model)
    input_requirement = capability.input_requirement
    if input_requirement == VideoModelInputRequirement.unsupported_multi_image:
        raise GenerationInputError(
            f"Video model '{model}' requires an unsupported multi-image flow"
        )

    if not input_assets:
        if input_requirement == VideoModelInputRequirement.image_required:
            raise GenerationInputError(
                f"Video model '{model}' requires one source image"
            )
        if input_requirement == VideoModelInputRequirement.video_required:
            raise GenerationInputError(
                f"Video model '{model}' requires one source video"
            )

        return VideoInputMode.text_to_video

    if input_requirement == VideoModelInputRequirement.text_only:
        raise GenerationInputError(
            f"Video model '{model}' only supports text-to-video generation"
        )

    source = validate_video_source_metadata(
        input_asset=input_assets[0],
        require_download_url=require_download_url,
    )

    if source.media_kind == VideoSourceMediaKind.video:
        max_size_bytes = (
            settings.max_video_source_video_size_bytes
            if settings is not None
            else MAX_VIDEO_SOURCE_VIDEO_SIZE_BYTES
        )
        if (
            source.media_kind not in capability.accepted_source_media_kinds
            or input_requirement != VideoModelInputRequirement.video_required
        ):
            raise GenerationInputError(
                f"Video model '{model}' does not support video source inputs"
            )
        if source.content_type not in ALLOWED_VIDEO_SOURCE_VIDEO_CONTENT_TYPES:
            supported_types = ", ".join(
                sorted(ALLOWED_VIDEO_SOURCE_VIDEO_CONTENT_TYPES)
            )
            raise GenerationInputError(
                f"The source video type is not supported. Use one of: {supported_types}"
            )
        if source.size_bytes > max_size_bytes:
            raise GenerationInputError(
                "The source video must be "
                f"{format_size_limit(max_size_bytes)} or smaller"
            )
        if not capability.provider_source_routing_implemented:
            raise GenerationInputError(
                f"Video-to-video support is verified for model '{model}', but "
                "backend provider routing is not enabled yet"
            )
        if settings is None or not settings.genblaze_video_to_video_enabled:
            raise GenerationInputError(
                "Video-to-video generation is disabled by configuration"
            )
        if not video_to_video_routing_enabled(model, settings=settings):
            raise GenerationInputError(
                f"Video model '{model}' is not the configured video edit model"
            )

        return VideoInputMode.video_to_video

    if (
        source.media_kind not in capability.accepted_source_media_kinds
        or input_requirement == VideoModelInputRequirement.video_required
    ):
        raise GenerationInputError(f"Video model '{model}' requires one source video")
    if source.content_type not in ALLOWED_VIDEO_SOURCE_IMAGE_CONTENT_TYPES:
        supported_types = ", ".join(sorted(ALLOWED_VIDEO_SOURCE_IMAGE_CONTENT_TYPES))
        raise GenerationInputError(
            "The video source image type is not supported. "
            f"Use one of: {supported_types}"
        )
    max_size_bytes = (
        settings.max_video_source_image_size_bytes
        if settings is not None
        else MAX_VIDEO_SOURCE_IMAGE_SIZE_BYTES
    )
    if source.size_bytes > max_size_bytes:
        raise GenerationInputError(
            "The video source image must be "
            f"{format_size_limit(max_size_bytes)} or smaller"
        )

    return VideoInputMode.image_to_video


def build_video_input_plan(
    *,
    model: str,
    source_input_assets: list[dict[str, Any]],
    context_assets: list[dict[str, Any]],
    settings: Settings | None = None,
) -> VideoInputPlan:
    mode = validate_video_input_assets(
        model=model,
        input_assets=source_input_assets,
        settings=settings,
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
            settings=self.settings,
        )
        capability = require_video_model_capability(model)
        (
            pipeline_parameters,
            input_assets_parameter,
        ) = build_video_pipeline_parameters(
            parameters=request.parameters,
            input_assets=input_plan.provider_input_assets,
            capability=capability,
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
                    "provider_source_parameter": (
                        capability.provider_source_parameter
                        if input_plan.mode == VideoInputMode.video_to_video
                        else None
                    ),
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
