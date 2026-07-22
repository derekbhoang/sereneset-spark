import unittest

from app.models.asset import ReviewStatus
from app.models.generation_job import GenerationJobStatus
from app.services.video_model_capabilities import (
    VideoModelInputRequirement,
    VideoSourceMediaKind,
    get_video_model_capability,
)
from app.services.video_refinement import (
    ACTIVE_VIDEO_JOB_STATUSES,
    VIDEO_REFINEMENT_CONTRACT,
    VideoGenerationOperation,
    VideoRefinementLabelState,
    is_active_video_job_status,
    video_refinement_version_label,
)


class VideoRefinementContractTests(unittest.TestCase):
    def test_refinement_appends_a_draft_version_from_the_latest_mp4(self) -> None:
        contract = VIDEO_REFINEMENT_CONTRACT

        self.assertEqual(
            contract.operation,
            VideoGenerationOperation.refinement,
        )
        self.assertEqual(contract.input_mode, "video_to_video")
        self.assertEqual(contract.source_role, "source_creative")
        self.assertEqual(contract.source_media_kind, VideoSourceMediaKind.video)
        self.assertEqual(contract.source_content_types, {"video/mp4"})
        self.assertEqual(contract.source_suffixes, {".mp4"})
        self.assertEqual(contract.review_status_on_queue, ReviewStatus.draft)
        self.assertTrue(contract.requires_latest_version)
        self.assertTrue(contract.requires_completed_source)
        self.assertTrue(contract.requires_stored_source_artifact)
        self.assertTrue(contract.requires_source_sha256)
        self.assertTrue(contract.appends_to_source_asset)

    def test_refinement_does_not_allow_client_routing_overrides(self) -> None:
        contract = VIDEO_REFINEMENT_CONTRACT

        self.assertFalse(contract.allows_client_source_override)
        self.assertFalse(contract.allows_client_model_override)
        self.assertFalse(contract.allows_generation_controls)

    def test_provider_contract_matches_the_registered_edit_model(self) -> None:
        capability = get_video_model_capability("wan2.7-videoedit")

        self.assertIsNotNone(capability)
        assert capability is not None
        self.assertEqual(
            capability.input_requirement,
            VideoModelInputRequirement.video_required,
        )
        self.assertEqual(
            capability.provider_allowed_parameters,
            VIDEO_REFINEMENT_CONTRACT.provider_parameters,
        )
        self.assertEqual(capability.provider_source_parameter, "video")
        self.assertTrue(capability.provider_source_routing_implemented)

    def test_only_queued_and_running_jobs_are_active(self) -> None:
        self.assertEqual(
            ACTIVE_VIDEO_JOB_STATUSES,
            {"queued", "running"},
        )
        self.assertEqual(
            VIDEO_REFINEMENT_CONTRACT.max_active_jobs_per_asset,
            1,
        )
        self.assertTrue(is_active_video_job_status(GenerationJobStatus.queued))
        self.assertTrue(is_active_video_job_status("running"))
        self.assertFalse(is_active_video_job_status("succeeded"))
        self.assertFalse(is_active_video_job_status("failed"))
        self.assertFalse(is_active_video_job_status("canceled"))

    def test_refinement_labels_are_stable(self) -> None:
        expected_labels = {
            VideoRefinementLabelState.queued: "Queued video refinement 4",
            VideoRefinementLabelState.completed: "Video refinement 4",
            VideoRefinementLabelState.canceled: "Canceled video refinement 4",
        }

        for state, expected_label in expected_labels.items():
            with self.subTest(state=state):
                self.assertEqual(
                    video_refinement_version_label(
                        version_number=4,
                        state=state,
                    ),
                    expected_label,
                )

    def test_refinement_label_rejects_invalid_version_number(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be positive"):
            video_refinement_version_label(
                version_number=0,
                state=VideoRefinementLabelState.queued,
            )


if __name__ == "__main__":
    unittest.main()
