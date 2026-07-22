from dataclasses import dataclass
from enum import Enum

from app.models.asset import ReviewStatus
from app.models.generation_job import GenerationJobStatus
from app.services.video_model_capabilities import VideoSourceMediaKind


class VideoGenerationOperation(str, Enum):
    generation = "video_generation"
    refinement = "video_refinement"


class VideoRefinementLabelState(str, Enum):
    queued = "queued"
    completed = "completed"
    canceled = "canceled"


ACTIVE_VIDEO_JOB_STATUSES = frozenset(
    {
        GenerationJobStatus.queued.value,
        GenerationJobStatus.running.value,
    }
)


@dataclass(frozen=True)
class VideoRefinementContract:
    operation: VideoGenerationOperation = VideoGenerationOperation.refinement
    input_mode: str = "video_to_video"
    source_role: str = "source_creative"
    source_media_kind: VideoSourceMediaKind = VideoSourceMediaKind.video
    source_content_types: frozenset[str] = frozenset({"video/mp4"})
    source_suffixes: frozenset[str] = frozenset({".mp4"})
    provider_parameters: frozenset[str] = frozenset({"prompt", "video"})
    review_status_on_queue: ReviewStatus = ReviewStatus.draft
    max_active_jobs_per_asset: int = 1
    requires_latest_version: bool = True
    requires_completed_source: bool = True
    requires_stored_source_artifact: bool = True
    requires_source_sha256: bool = True
    appends_to_source_asset: bool = True
    allows_client_source_override: bool = False
    allows_client_model_override: bool = False
    allows_generation_controls: bool = False


VIDEO_REFINEMENT_CONTRACT = VideoRefinementContract()


def is_active_video_job_status(status: str | GenerationJobStatus) -> bool:
    value = status.value if isinstance(status, GenerationJobStatus) else status
    return value in ACTIVE_VIDEO_JOB_STATUSES


def video_refinement_version_label(
    *,
    version_number: int,
    state: VideoRefinementLabelState,
) -> str:
    if version_number < 1:
        raise ValueError("Video refinement version number must be positive")

    labels = {
        VideoRefinementLabelState.queued: (
            f"Queued video refinement {version_number}"
        ),
        VideoRefinementLabelState.completed: f"Video refinement {version_number}",
        VideoRefinementLabelState.canceled: (
            f"Canceled video refinement {version_number}"
        ),
    }
    return labels[state]
