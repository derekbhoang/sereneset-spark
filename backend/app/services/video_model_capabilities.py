from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class VideoModelInputRequirement(str, Enum):
    unsupported_multi_image = "unsupported_multi_image"
    image_required = "image_required"
    image_optional = "image_optional"
    text_only = "text_only"
    video_required = "video_required"


class VideoSourceMediaKind(str, Enum):
    image = "image"
    video = "video"


@dataclass(frozen=True)
class VideoModelCapability:
    model_id: str
    input_requirement: VideoModelInputRequirement
    aliases: tuple[str, ...] = ()
    provider: str = "gmicloud"
    accepted_source_media_kinds: frozenset[VideoSourceMediaKind] = field(
        default_factory=frozenset
    )
    allowed_duration_seconds: tuple[int, ...] | None = None
    allowed_aspect_ratios: tuple[str, ...] | None = None
    allowed_resolutions: tuple[str, ...] | None = None
    provider_parameter_aliases: tuple[tuple[str, str], ...] = ()
    provider_required_parameters: frozenset[str] = field(default_factory=frozenset)
    provider_integer_string_parameters: frozenset[str] = field(
        default_factory=frozenset
    )
    provider_allowed_parameters: frozenset[str] | None = None
    provider_source_parameter: str | None = None
    provider_source_routing_implemented: bool = False

    def __post_init__(self) -> None:
        if self.provider_allowed_parameters is not None:
            missing_required_parameters = (
                self.provider_required_parameters
                - self.provider_allowed_parameters
            )
            if missing_required_parameters:
                raise ValueError(
                    "Required provider parameters must be included in the allowlist"
                )

        if self.provider_source_routing_implemented:
            if self.provider_source_parameter is None:
                raise ValueError(
                    "Implemented source routing requires a provider source parameter"
                )
            if (
                self.provider_source_parameter
                not in self.provider_required_parameters
            ):
                raise ValueError(
                    "The routed provider source parameter must be required"
                )

    @property
    def registered_names(self) -> tuple[str, ...]:
        return (self.model_id, *self.aliases)


GMI_VEO_CAPABILITY = VideoModelCapability(
    model_id="veo-3.1-fast-generate-001",
    aliases=("Veo3-Fast",),
    input_requirement=VideoModelInputRequirement.image_optional,
    accepted_source_media_kinds=frozenset({VideoSourceMediaKind.image}),
    allowed_duration_seconds=(4, 6, 8),
    allowed_aspect_ratios=("16:9", "9:16"),
    allowed_resolutions=("720p", "1080p"),
    provider_parameter_aliases=(
        ("duration", "durationSeconds"),
        ("aspect_ratio", "aspectRatio"),
    ),
    provider_required_parameters=frozenset({"durationSeconds"}),
    provider_integer_string_parameters=frozenset({"durationSeconds"}),
)


VIDEO_MODEL_CAPABILITIES = (
    GMI_VEO_CAPABILITY,
    VideoModelCapability(
        model_id="Kling-Image2Video-V2.1-Master",
        input_requirement=VideoModelInputRequirement.image_required,
        accepted_source_media_kinds=frozenset({VideoSourceMediaKind.image}),
    ),
    VideoModelCapability(
        model_id="pixverse-v5.6-i2v",
        input_requirement=VideoModelInputRequirement.image_required,
        accepted_source_media_kinds=frozenset({VideoSourceMediaKind.image}),
    ),
    VideoModelCapability(
        model_id="wan2.6-r2v",
        input_requirement=VideoModelInputRequirement.image_required,
        accepted_source_media_kinds=frozenset({VideoSourceMediaKind.image}),
    ),
    VideoModelCapability(
        model_id="Kling-Text2Video-V2.1-Master",
        input_requirement=VideoModelInputRequirement.text_only,
    ),
    VideoModelCapability(
        model_id="pixverse-v5.6-t2v",
        input_requirement=VideoModelInputRequirement.text_only,
    ),
    VideoModelCapability(
        model_id="pixverse-v5.6-transition",
        input_requirement=VideoModelInputRequirement.unsupported_multi_image,
        accepted_source_media_kinds=frozenset({VideoSourceMediaKind.image}),
    ),
    VideoModelCapability(
        model_id="wan2.7-videoedit",
        input_requirement=VideoModelInputRequirement.video_required,
        accepted_source_media_kinds=frozenset({VideoSourceMediaKind.video}),
        provider_allowed_parameters=frozenset({"prompt", "video"}),
        provider_required_parameters=frozenset({"prompt", "video"}),
        provider_source_parameter="video",
        provider_source_routing_implemented=True,
    ),
)


def normalize_video_model_id(model: str) -> str:
    return model.strip().casefold()


def build_video_model_capability_registry(
    capabilities: tuple[VideoModelCapability, ...],
) -> Mapping[str, VideoModelCapability]:
    registry: dict[str, VideoModelCapability] = {}
    for capability in capabilities:
        for registered_name in capability.registered_names:
            normalized_name = normalize_video_model_id(registered_name)
            if not normalized_name:
                raise ValueError("Video model capability names must not be empty")
            if normalized_name in registry:
                raise ValueError(
                    f"Duplicate video model capability name: {registered_name}"
                )
            registry[normalized_name] = capability

    return MappingProxyType(registry)


VIDEO_MODEL_CAPABILITY_REGISTRY = build_video_model_capability_registry(
    VIDEO_MODEL_CAPABILITIES
)


def get_video_model_capability(model: str) -> VideoModelCapability | None:
    return VIDEO_MODEL_CAPABILITY_REGISTRY.get(normalize_video_model_id(model))
